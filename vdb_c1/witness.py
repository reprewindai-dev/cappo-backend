"""VDB-C1 witness (W1 profile): countersigns journal history, only forward.

The witness is a separate role with its own Ed25519 key and its own durable
state (`state.json`). It never takes the coordinator's word for anything:

1. It reads the journal file itself, read-only, and re-checks the whole hash
   chain from genesis.
2. It requires the new journal to extend the last head it signed: same
   genesis, and the record at its last seq still has the hash it signed.
   Otherwise it refuses and writes signed fork evidence.
3. It re-verifies every newly FINALIZED intent with the offline verifier
   (`verify.py`), against the trust store it was created with. One invalid
   finality and it refuses to sign.
4. It signs a checkpoint `{journal_genesis_hash, seq, head_hash}` and
   persists its new state atomically (write, fsync, rename).

What this adds over L1: a finalizer can no longer rewrite or fork history it
already showed the witness without the fork being provable. That covers
dropping a finality, re-finalizing differently, or showing two parties two
different journals. What it does not add: the witness sees the journal, not
the SQLite store, so a finalizer that lies consistently about the store in
records the witness has not yet seen is still not caught by the witness.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vdb_c1 import canon
from vdb_c1.coordinator import load_key, save_key
from vdb_c1.verify import verify_bundle

CHECKPOINT_PROTOCOL = "veklom.vdb.c1.checkpoint/v1"
FORK_PROTOCOL = "veklom.vdb.c1.fork-evidence/v1"
JOURNAL_PROTOCOL = "veklom.vdb.c1.journal/v1"


class WitnessRefused(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def _read_journal(path: pathlib.Path) -> list[dict]:
    """Strict, read-only: any torn or bad line is a refusal, never a repair."""
    data = pathlib.Path(path).read_bytes()
    if data and not data.endswith(b"\n"):
        raise WitnessRefused("E_TORN_JOURNAL", "journal does not end with a complete record")
    records, prev = [], "genesis"
    for seq, line in enumerate(data.splitlines()):
        rec = json.loads(line)
        body = {k: v for k, v in rec.items() if k != "hash"}
        if (rec.get("protocol") != JOURNAL_PROTOCOL or rec.get("seq") != seq
                or rec.get("prev") != prev or rec.get("hash") != canon.digest(body)):
            raise WitnessRefused("E_CHAIN", f"record {seq}")
        records.append(rec)
        prev = rec["hash"]
    if not records:
        raise WitnessRefused("E_EMPTY", "nothing to witness")
    return records


def _atomic_write(path: pathlib.Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class Witness:
    def __init__(self, wdir: pathlib.Path) -> None:
        self.dir = pathlib.Path(wdir)
        self.sk, self.kid = load_key(self.dir / "witness.key")
        self.trust = json.loads((self.dir / "trust_store.json").read_text())
        self.state = json.loads((self.dir / "state.json").read_text())

    @classmethod
    def create(cls, wdir: pathlib.Path, trust_store: dict) -> dict:
        """trust_store: the authority and finalizer keys this witness pins."""
        wdir = pathlib.Path(wdir)
        wdir.mkdir(parents=True)
        sk = Ed25519PrivateKey.generate()
        kid = save_key(wdir / "witness.key", sk)
        pinned = {"authority_keys": trust_store["authority_keys"],
                  "finalizer_keys": trust_store["finalizer_keys"]}
        (wdir / "trust_store.json").write_text(json.dumps(pinned, indent=1))
        _atomic_write(wdir / "state.json", {"genesis_hash": None, "seq": None, "head_hash": None})
        return {kid: canon.b64u(canon.public_raw(sk))}

    def public_keys(self) -> dict[str, str]:
        return {self.kid: canon.b64u(canon.public_raw(self.sk))}

    def _fork(self, records: list[dict], reason: str, detail: str) -> None:
        seq = self.state["seq"]
        evidence = canon.sign({
            "protocol": FORK_PROTOCOL,
            "reason": reason,
            "detail": detail,
            "witnessed": {k: self.state[k] for k in ("genesis_hash", "seq", "head_hash")},
            "presented": {"genesis_hash": records[0]["hash"], "length": len(records),
                          "hash_at_witnessed_seq": records[seq]["hash"] if seq < len(records) else None},
        }, self.sk, self.kid)
        forks = self.dir / "forks"
        forks.mkdir(exist_ok=True)
        (forks / f"fork-{len(list(forks.iterdir())):04d}.json").write_text(json.dumps(evidence, indent=1))
        raise WitnessRefused(reason, detail)

    def observe(self, journal_path: pathlib.Path, at: str | None = None) -> dict:
        records = _read_journal(journal_path)
        st = self.state
        start = 0
        if st["seq"] is not None:
            if records[0]["hash"] != st["genesis_hash"]:
                self._fork(records, "E_DIFFERENT_JOURNAL", "genesis record differs")
            if len(records) <= st["seq"]:
                self._fork(records, "E_ROLLBACK", f"journal shorter than witnessed seq {st['seq']}")
            if records[st["seq"]]["hash"] != st["head_hash"]:
                self._fork(records, "E_FORK", f"record {st['seq']} differs from the witnessed head")
            start = st["seq"] + 1
        for rec in records[start:]:
            if rec["kind"] != "FINALIZED":
                continue
            bundle = {
                "protocol": "veklom.vdb.c1.bundle/v1",
                "intent_id": rec["intent_id"],
                "journal": records[: rec["seq"] + 1],
                "receipt": {"certificate": rec["body"]["certificate"],
                            "certificate_digest": canon.digest(rec["body"]["certificate"])},
            }
            verdict = verify_bundle(bundle, self.trust)
            if verdict["verdict"] != "VALID_FINALIZED":
                raise WitnessRefused("E_INVALID_FINALITY",
                                     f"seq {rec['seq']}: {verdict['verdict']} ({verdict['failed_check']})")
        head = records[-1]
        checkpoint = canon.sign({
            "protocol": CHECKPOINT_PROTOCOL,
            "journal_genesis_hash": records[0]["hash"],
            "seq": head["seq"],
            "head_hash": head["hash"],
            "witnessed_at": at or canon.format_time(time.time()),
            "witness_kid": self.kid,
        }, self.sk, self.kid)
        with open(self.dir / "checkpoints.jsonl", "ab") as f:
            f.write(canon.jcs(checkpoint) + b"\n")
            f.flush()
            os.fsync(f.fileno())
        self.state = {"genesis_hash": records[0]["hash"], "seq": head["seq"], "head_hash": head["hash"]}
        _atomic_write(self.dir / "state.json", self.state)
        return checkpoint

    def checkpoints(self) -> list[dict]:
        p = self.dir / "checkpoints.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def covering(self, seq: int) -> dict | None:
        """The earliest checkpoint whose head is at or after `seq`."""
        return next((c for c in self.checkpoints() if c["payload"]["seq"] >= seq), None)
