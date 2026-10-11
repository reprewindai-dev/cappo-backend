"""VDB-C1.0 coordinator: the one trusted finalizer (see PROTOCOL.md).

Every submit runs under an exclusive file lock, recovers unterminated
intents first, and follows the normative order:

  admission -> precondition -> PREPARED -> BEGIN -> conditional UPSERT
  -> COMMITTING -> COMMIT -> fresh read -> COMMITTED -> sign -> FINALIZED

Crash points kill the process with os._exit (no cleanup, no flush of
anything not already fsynced). They exist only for the crash matrix and are
inert during recovery.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pathlib
import time
import uuid
from collections.abc import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from vdb_c1 import canon
from vdb_c1 import states as S
from vdb_c1.journal import Journal, JournalCorrupt
from vdb_c1.state_adapter import PreconditionFailed, SqliteStateAdapter

__all__ = ["CRASH_EXIT", "CRASH_POINTS", "Coordinator", "JournalCorrupt", "RecoveryError",
           "init_home", "make_intent"]

INTENT_PROTOCOL = "veklom.vdb.c1.intent/v1"
FINALITY_PROTOCOL = "veklom.vdb.c1.finality/v1"
BUNDLE_PROTOCOL = "veklom.vdb.c1.bundle/v1"
TRUST_PROTOCOL = "veklom.vdb.c1.trust/v1"

CRASH_EXIT = 77
CRASH_POINTS = ("after_prepared", "before_storage_commit", "after_storage_commit",
                "after_committed_record", "after_finalized")

ENVELOPE_KEYS = {"alg", "kid", "payload", "sig"}
INTENT_KEYS = {"protocol", "intent_id", "namespace", "op", "key", "value", "expected_generation",
               "expected_root", "issued_at", "expires_at", "authority_kid"}
MAX_TTL_S = 600
CLOCK_SKEW_S = 5
MAX_KEY = 256
MAX_VALUE = 65536


class RecoveryError(Exception):
    """The journal and the state store disagree in a way the protocol forbids."""


# ---------------------------------------------------------------- setup

def _write_private(path: pathlib.Path, obj: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)


def save_key(path: pathlib.Path, sk: Ed25519PrivateKey) -> str:
    raw = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    kid = canon.key_id(canon.public_raw(sk))
    _write_private(pathlib.Path(path), {"kid": kid, "private_key": canon.b64u(raw)})
    return kid


def load_key(path: pathlib.Path) -> tuple[Ed25519PrivateKey, str]:
    data = json.loads(pathlib.Path(path).read_text())
    sk = Ed25519PrivateKey.from_private_bytes(canon.unb64u(data["private_key"]))
    if canon.key_id(canon.public_raw(sk)) != data["kid"]:
        raise ValueError("key file kid does not match its key")
    return sk, data["kid"]


def init_home(home: pathlib.Path, namespace: str, authority_keys: dict[str, str]) -> dict:
    """Create the state store, an empty journal, the finalizer key and the
    public trust store. authority_keys maps kid -> base64url public key."""
    home = pathlib.Path(home)
    home.mkdir(parents=True)
    SqliteStateAdapter.create(home / "state.db", namespace)
    sk = Ed25519PrivateKey.generate()
    fkid = save_key(home / "finalizer.key", sk)
    for kid, pub in authority_keys.items():
        if canon.key_id(canon.unb64u(pub)) != kid:
            raise ValueError(f"authority kid {kid} does not match its key")
    trust = {"protocol": TRUST_PROTOCOL, "authority_keys": dict(authority_keys),
             "finalizer_keys": {fkid: canon.b64u(canon.public_raw(sk))}}
    (home / "trust_store.json").write_text(json.dumps(trust, indent=1))
    return trust


def make_intent(sk: Ed25519PrivateKey, kid: str, namespace: str, key: str, value: str, *,
                expected_generation: int, expected_root: str, ttl_s: int = 300,
                now: float | None = None, intent_id: str | None = None) -> dict:
    now = time.time() if now is None else now
    return canon.sign({
        "protocol": INTENT_PROTOCOL,
        "intent_id": intent_id or str(uuid.uuid4()),
        "namespace": namespace,
        "op": "UPSERT",
        "key": key,
        "value": value,
        "expected_generation": expected_generation,
        "expected_root": expected_root,
        "issued_at": canon.format_time(now),
        "expires_at": canon.format_time(now + ttl_s),
        "authority_kid": kid,
    }, sk, kid)


# ---------------------------------------------------------------- coordinator

class Coordinator:
    def __init__(self, home: pathlib.Path, *, crash_at: str | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        if crash_at is not None and crash_at not in CRASH_POINTS:
            raise ValueError(f"unknown crash point {crash_at}")
        self.home = pathlib.Path(home)
        self.crash_at = crash_at
        self.clock = clock
        self.adapter = SqliteStateAdapter(self.home / "state.db")
        trust = json.loads((self.home / "trust_store.json").read_text())
        self.authority_keys = trust["authority_keys"]
        self.finalizer_sk, self.finalizer_kid = load_key(self.home / "finalizer.key")
        self.journal = Journal(self.home / "journal.jsonl")
        self._recovering = False

    @classmethod
    def open(cls, home: pathlib.Path, **kw) -> "Coordinator":
        return cls(home, **kw)

    # -- plumbing --------------------------------------------------------
    @contextlib.contextmanager
    def _locked(self):
        fd = os.open(self.home / "finalizer.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.journal = Journal(self.home / "journal.jsonl")   # state as of lock
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _now(self) -> str:
        return canon.format_time(self.clock())

    def _crash(self, point: str) -> None:
        if self.crash_at == point and not self._recovering:
            os._exit(CRASH_EXIT)

    def _binding(self, intent_id: str) -> list[dict]:
        return [r for r in self.journal.for_intent(intent_id) if r["kind"] in S.BINDING_KINDS]

    def _append(self, kind: str, intent_id: str, body: dict) -> dict:
        if kind in S.BINDING_KINDS:
            S.state_after([r["kind"] for r in self._binding(intent_id)] + [kind])
        return self.journal.append(kind, intent_id, body, self._now())

    def state(self) -> dict:
        snap = self.adapter.snapshot()
        return {k: snap[k] for k in ("namespace", "generation", "root", "entries")}

    # -- submit ------------------------------------------------------------
    def submit(self, signed: dict) -> dict:
        with self._locked():
            self._recover_locked()
            return self._submit_locked(signed)

    def _submit_locked(self, signed: dict) -> dict:
        payload = signed.get("payload") if isinstance(signed, dict) else None
        intent_id = (payload.get("intent_id") if isinstance(payload, dict)
                     and isinstance(payload.get("intent_id"), str) else "unknown")
        intent_hash = None
        if isinstance(payload, dict):
            try:
                intent_hash = canon.digest(payload)
            except Exception:
                intent_hash = None

        bound = self._binding(intent_id) if intent_id != "unknown" else []
        if bound:
            if bound[0]["body"]["intent_hash"] != intent_hash:
                return self._reject(intent_id, intent_hash, "E_INTENT_CONFLICT")
            if not canon.verify_signed(signed, self.authority_keys):
                return self._reject(intent_id, intent_hash, "E_BAD_SIGNATURE")
            return self._result(intent_id, replayed=True)

        reason = self._admission(signed)
        if reason:
            return self._reject(intent_id, intent_hash, reason)
        p = payload
        pre = self.adapter.snapshot()
        if pre["generation"] != p["expected_generation"]:
            reason = "E_STALE_GENERATION"
        elif pre["root"] != p["expected_root"]:
            reason = "E_ROOT_MISMATCH"
        if reason:
            self._append(S.PRECONDITION_REJECTED, intent_id, {
                "intent_hash": intent_hash, "reason": reason,
                "observed_generation": pre["generation"], "observed_root": pre["root"]})
            return {"intent_id": intent_id, "state": S.PRECONDITION_REJECTED, "reason": reason,
                    "replayed": False}

        self._append(S.PREPARED, intent_id, {
            "intent_hash": intent_hash,
            "signed_intent": signed,
            "admission": {"authority_kid": p["authority_kid"], "admitted_at": self._now(),
                          "checks": ["schema", "protocol", "op", "namespace", "kid",
                                     "trusted_key", "signature", "validity_window", "ttl"]},
            "store_id": pre["store_id"],
            "pre_generation": pre["generation"],
            "pre_root": pre["root"],
            "pre_entries": pre["entries"],
            "payload_commitment": canon.payload_commitment(p["op"], p["key"], p["value"]),
        })
        self._crash("after_prepared")

        def before_commit() -> None:
            self._append(S.COMMITTING, intent_id, {"intent_hash": intent_hash})
            self._crash("before_storage_commit")

        try:
            self.adapter.apply_upsert(
                intent_id=intent_id, intent_hash=intent_hash, key=p["key"], value=p["value"],
                expected_generation=p["expected_generation"], expected_root=p["expected_root"],
                before_commit=before_commit)
        except PreconditionFailed as e:
            # Rolled back before COMMITTING was journaled: PREPARED -> ABORTED.
            self._append(S.ABORTED, intent_id, {"intent_hash": intent_hash, "reason": e.reason})
            return self._result(intent_id, replayed=False)
        self._crash("after_storage_commit")
        self._confirm_commit(intent_id, recovered=False)
        return self._result(intent_id, replayed=False)

    def _reject(self, intent_id: str, intent_hash: str | None, reason: str) -> dict:
        self._append(S.ADMISSION_REJECTED, intent_id, {"intent_hash": intent_hash, "reason": reason})
        return {"intent_id": intent_id, "state": S.ADMISSION_REJECTED, "reason": reason,
                "replayed": False}

    def _admission(self, signed) -> str | None:
        if not isinstance(signed, dict) or set(signed) != ENVELOPE_KEYS:
            return "E_SCHEMA"
        p = signed["payload"]
        if not isinstance(p, dict) or set(p) != INTENT_KEYS:
            return "E_SCHEMA"
        ints = {"expected_generation"}
        for k in INTENT_KEYS:
            want = int if k in ints else str
            if type(p[k]) is not want:                      # rejects bool and float
                return "E_SCHEMA"
        if (p["expected_generation"] < 0 or len(p["key"]) > MAX_KEY or len(p["value"]) > MAX_VALUE
                or not p["key"]):
            return "E_SCHEMA"
        try:
            uuid.UUID(p["intent_id"])
            issued, expires = canon.parse_time(p["issued_at"]), canon.parse_time(p["expires_at"])
        except ValueError:
            return "E_SCHEMA"
        if p["protocol"] != INTENT_PROTOCOL:
            return "E_PROTOCOL"
        if p["op"] != "UPSERT":
            return "E_OP"
        if p["namespace"] != self.adapter.snapshot()["namespace"]:
            return "E_NAMESPACE"
        if signed["kid"] != p["authority_kid"]:
            return "E_KID_MISMATCH"
        if signed["kid"] not in self.authority_keys:
            return "E_UNTRUSTED_KEY"
        if not canon.verify_signed(signed, self.authority_keys):
            return "E_BAD_SIGNATURE"
        now = self.clock()
        if expires - issued > MAX_TTL_S or expires <= issued:
            return "E_TTL"
        if now < issued - CLOCK_SKEW_S:
            return "E_NOT_YET_VALID"
        if now >= expires:
            return "E_EXPIRED"
        return None

    # -- commit confirmation and finality -----------------------------------
    def _confirm_commit(self, intent_id: str, *, recovered: bool) -> bool:
        """Fresh-read proof of the durable commit, then COMMITTED and FINALIZED."""
        prepared = self._binding(intent_id)[0]
        pb = prepared["body"]
        p = pb["signed_intent"]["payload"]
        applied = self.adapter.applied(intent_id)
        fresh = self.adapter.snapshot()
        expected_entries = sorted([e for e in pb["pre_entries"] if e[0] != p["key"]]
                                  + [[p["key"], p["value"]]])
        problems = []
        if applied is None or applied["intent_hash"] != pb["intent_hash"]:
            problems.append("applied_row")
        if fresh["store_id"] != pb["store_id"]:
            problems.append("store_id")
        if applied and not (fresh["generation"] == applied["generation"] == pb["pre_generation"] + 1):
            problems.append("generation")
        if applied and fresh["root"] != applied["post_root"]:
            problems.append("post_root")
        if fresh["entries"] != expected_entries:
            problems.append("entries")
        if problems:
            if self._binding(intent_id)[-1]["kind"] != S.COMMIT_UNCERTAIN:
                self._append(S.COMMIT_UNCERTAIN, intent_id, {
                    "intent_hash": pb["intent_hash"],
                    "reason": "E_FRESH_READ_MISMATCH:" + ",".join(problems)})
            return False
        self._append(S.COMMITTED_RECORD, intent_id, {
            "intent_hash": pb["intent_hash"],
            "post_generation": fresh["generation"],
            "post_root": fresh["root"],
            "post_entries": fresh["entries"],
            "applied_row": applied,
            "physical": {**self.adapter.durability(), "store_id": fresh["store_id"]},
            "recovered": recovered,
        })
        self._crash("after_committed_record")
        self._finalize(intent_id)
        return True

    def _certificate(self, intent_id: str) -> dict:
        recs = self._binding(intent_id)
        prepared = recs[0]
        committed = next(r for r in recs if r["kind"] == S.COMMITTED_RECORD)
        pb, cb = prepared["body"], committed["body"]
        p = pb["signed_intent"]["payload"]
        return canon.sign({
            "protocol": FINALITY_PROTOCOL,
            "intent_id": intent_id,
            "intent_hash": pb["intent_hash"],
            "namespace": p["namespace"],
            "authority_kid": p["authority_kid"],
            "expected_generation": p["expected_generation"],
            "pre_root": pb["pre_root"],
            "payload_commitment": pb["payload_commitment"],
            "post_generation": cb["post_generation"],
            "post_root": cb["post_root"],
            "physical": {**cb["physical"], "applied_row": cb["applied_row"]},
            "journal": {"prepared_seq": prepared["seq"], "prepared_hash": prepared["hash"],
                        "committed_seq": committed["seq"], "committed_hash": committed["hash"]},
            "finalized_at": committed["at"],
            "finalizer_kid": self.finalizer_kid,
        }, self.finalizer_sk, self.finalizer_kid)

    def _finalize(self, intent_id: str) -> None:
        cert = self._certificate(intent_id)
        self._append(S.FINALIZED, intent_id, {"intent_hash": cert["payload"]["intent_hash"],
                                              "certificate": cert})
        self._crash("after_finalized")

    def _result(self, intent_id: str, *, replayed: bool) -> dict:
        recs = self._binding(intent_id)
        state = S.state_after([r["kind"] for r in recs])
        out = {"intent_id": intent_id, "state": state, "replayed": replayed}
        last = recs[-1]
        if state == S.FINALIZED:
            out["certificate"] = last["body"]["certificate"]
        elif "reason" in last["body"]:
            out["reason"] = last["body"]["reason"]
        return out

    # -- recovery ------------------------------------------------------------
    def recover(self) -> list[dict]:
        with self._locked():
            return self._recover_locked()

    def _recover_locked(self) -> list[dict]:
        report = []
        self._recovering = True
        try:
            seen = []
            for r in self.journal.records:
                if r["kind"] == S.PREPARED and r["intent_id"] not in seen:
                    seen.append(r["intent_id"])
            for iid in seen:
                kinds = [r["kind"] for r in self._binding(iid)]
                before = S.state_after(kinds)
                if before in S.TERMINAL:
                    continue
                self._recover_one(iid, kinds)
                report.append({"intent_id": iid, "from": before,
                               "to": S.state_after([r["kind"] for r in self._binding(iid)])})
        finally:
            self._recovering = False
        return report

    def _recover_one(self, iid: str, kinds: list[str]) -> None:
        ih = self._binding(iid)[0]["body"]["intent_hash"]
        last = kinds[-1]
        applied = self.adapter.applied(iid)
        if last == S.PREPARED:
            if applied is not None:
                raise RecoveryError(f"{iid}: applied row exists but COMMITTING was never journaled")
            self._append(S.ABORTED, iid, {"intent_hash": ih,
                                          "reason": "E_CRASH_BEFORE_STORAGE_TRANSACTION"})
            return
        if last == S.COMMITTED_RECORD:
            self._finalize(iid)
            return
        if last == S.COMMITTING:
            self._append(S.COMMIT_UNCERTAIN, iid, {"intent_hash": ih,
                                                   "reason": "E_OUTCOME_UNKNOWN_AFTER_CRASH"})
        if applied is None:
            self._append(S.ABORTED, iid, {"intent_hash": ih, "reason": "E_STORAGE_COMMIT_NOT_DURABLE"})
        else:
            self._confirm_commit(iid, recovered=True)

    # -- export ------------------------------------------------------------------
    def export_bundle(self, intent_id: str) -> dict:
        with self._locked():
            fin = [r for r in self.journal.for_intent(intent_id) if r["kind"] == S.FINALIZED]
            if len(fin) != 1:
                raise ValueError(f"{intent_id} has {len(fin)} FINALIZED records")
            cert = fin[0]["body"]["certificate"]
            # Round-trip through JSON, as a bundle file would be: the receipt
            # must not alias the journal's FINALIZED record. (copy.deepcopy
            # keeps internal aliases, so it is not enough.)
            return json.loads(json.dumps({
                "protocol": BUNDLE_PROTOCOL,
                "intent_id": intent_id,
                "journal": self.journal.records[: fin[0]["seq"] + 1],
                "receipt": {"certificate": cert, "certificate_digest": canon.digest(cert)},
            }))
