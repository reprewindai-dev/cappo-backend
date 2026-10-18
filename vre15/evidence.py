"""Revocations as verifiable evidence: residency follows authority, signed.

`vre15/revoke.py` checks out a tenant's state when its authority ends. This
module turns each revocation into evidence that a third party can check
offline with `vre15/verify_evidence.py`, which shares no code with this file.

Three roles, three keys:
- the runtime (issuer) appends one record per revocation to a hash-chained,
  fsynced journal. Each record carries an EEE v0.1.0 envelope signed with the
  issuer key, in the wire format CAPPO's own EEEVerifier checks
  (cappo_backend/services/eee.py);
- a witness, holding its own key and state, re-reads the journal, re-verifies
  every new record, and countersigns a checkpoint of the head. It only moves
  forward: a rewritten, shortened or substituted journal is refused, with
  signed fork evidence;
- the verifier holds only the public trust store.

What an envelope says, and why it says it that way:
- The action is the runtime's custody duty, carried out under the runtime's
  custody grant for this run (`authority_window` = the grant). It is not an
  action under the tenant's authority, which has already ended: EEE requires
  an execution to start inside its authority window (§7.6), and a
  revocation always starts after the tenant's window closes.
- The tenant's ended window is the subject. It is committed in
  `input_commitment`, and the first policy decision records it.
- `output_commitment` binds the receipt digest, the exact runs of pages
  written and the measured post-condition (`held_privately`, from pagemap).
  The runtime does not hash whole images: reading a 4 MiB image inside the
  tenant cgroup evicted live tenants (run 20261011T230741Z). The witness,
  outside the cgroup, hashes the final images and puts them in its
  checkpoint instead.
- `status` is "completed" only when no private page was held after the
  revocation; otherwise "error" with a RESIDENCY_RETAINED violation.
- `revocation_check` is "none": the custody grant has no revocation list, so
  verifiers report REVOCATION_NOT_CHECKED, as for VDB-C1.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import queue
import threading
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vdb_c1 import canon
from vdb_c1.coordinator import load_key, save_key
from vre15 import verify_evidence

JOURNAL_PROTOCOL = verify_evidence.JOURNAL
BUNDLE_PROTOCOL = verify_evidence.BUNDLE
CHECKPOINT_PROTOCOL = verify_evidence.CHECKPOINT
FORK_PROTOCOL = "veklom.vre15.revocation-fork/v1"
CUSTODY_PROTOCOL = "veklom.vre15.custody-grant/v1"
EEE_VERSION = "0.1.0"
CAPABILITY_ID = "vre15.residency.revoke"
POLICY_BUNDLE_ID = "vre15-residency-follows-authority-v1"
POLICY_RULES = ["revoke.after_authority_end", "revoke.writeback_modified", "revoke.fdatasync",
                "revoke.remap", "revoke.no_private_pages"]
HERE = pathlib.Path(__file__).resolve().parent


def enforcer() -> dict:
    h = hashlib.sha256()
    for name in ("revoke.py", "evidence.py"):
        h.update((HERE / name).read_bytes())
    return {"name": "vre15", "version": "1.0.0", "build_hash": "sha256:" + h.hexdigest()}


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def custody_grant(run_id: str, not_before: float, not_after: float) -> dict:
    return {"protocol": CUSTODY_PROTOCOL, "run_id": run_id, "capability_id": CAPABILITY_ID,
            "not_before": canon.format_time(not_before), "not_after": canon.format_time(not_after)}


def _decision(rule, decision, at, reason):
    return {"gate": "residency", "rule_id": rule, "decision": decision, "evaluated_at": at,
            "latency_ms": 0, "reason_code": reason}


def envelope(seq: int, tenant: int, body: dict, custody: dict, sk: Ed25519PrivateKey, kid: str) -> dict:
    """Build and sign the EEE envelope for one revocation record body."""
    run_id = custody["run_id"]
    issuer = f"urn:veklom:vre15:{run_id}"
    started, done = body["revocation"]["started_at"], body["revocation"]["completed_at"]
    held = body["post_condition"]["held_privately"]
    rc = body["receipt"]
    released = held == 0
    effects = [f"state:checkpoint:tenant-{tenant}", f"memory:release:tenant-{tenant}"]
    env = {
        "eee_version": EEE_VERSION,
        "hash_alg": "SHA-256",
        "execution_id": f"{run_id}:revoke:{seq}",
        "idempotency_key": f"{run_id}:tenant-{tenant}:{body['authority']['window'][1]}",
        "issuer": issuer,
        "enforcer": enforcer(),
        "participant_identity": {"scheme": "veklom-runtime", "identifier": issuer},
        "principal": {"scheme": "veklom-tenant", "identifier": f"tenant-{tenant}"},
        "capability_id": CAPABILITY_ID,
        "capability_hash": canon.digest({"capability_id": CAPABILITY_ID, "op": "REVOKE_RESIDENCY",
                                         "tenant": tenant}),
        "capability_attenuation": {"resource_allowlist": [f"tenant-{tenant}"],
                                   "argument_constraints": {"region_pages": body["post_condition"]["region_pages"]},
                                   "delegation_depth": 0, "delegation_depth_max": 0},
        "runtime_lineage": {"model": "none", "model_version": "none", "framework": "vre15",
                            "framework_version": "1.0.0",
                            "config_hash": canon.digest({"policy": "mapped-ckpt", "sync": "fdatasync",
                                                         "release": "remap+MADV_PAGEOUT"})},
        "authority_chain": [{"type": "custody-grant", "artifact_hash": canon.digest(custody),
                             "issuer": issuer, "granted_at": custody["not_before"],
                             "expires_at": custody["not_after"]}],
        "authority_window": {"not_before": custody["not_before"], "not_after": custody["not_after"]},
        "revocation_check": {"method": "none", "checked_at": started,
                             "result": "custody grant has no revocation list"},
        "policy_bundle_id": POLICY_BUNDLE_ID,
        "policy_hash": canon.digest({"bundle_id": POLICY_BUNDLE_ID, "rules": POLICY_RULES}),
        "policy_decisions": [
            _decision("revoke.after_authority_end", "allow", started, "TENANT_AUTHORITY_ENDED"),
            _decision("revoke.no_private_pages", "allow" if released else "deny", done,
                      "NO_PRIVATE_PAGES" if released else "PRIVATE_PAGES_RETAINED"),
        ],
        "enforcement_mode": "fail-closed",
        "input_commitment": canon.digest({"tenant_authority": body["authority"]["digest"],
                                          "window": body["authority"]["window"]}),
        "effect_grammar": "EEE-EFFECTS-1",
        "allowed_effects": effects,
        "actual_effects": effects if released else effects[:1],
        "tool_actions": [{"tool": "vre15.revoke", "action_hash": "sha256:" + rc["digest"],
                          "decision": "released" if released else "retained",
                          "evidence_ref": f"journal:{seq}"}],
        "budget": {"granted": {"revocations": 1}, "consumed": {"revocations": 1}},
        "started_at": started,
        "ended_at": done,
        "output_commitment": canon.digest({"receipt_digest": rc["digest"], "runs": rc["runs"],
                                           "image": body["image"]["name"], "held_privately": held}),
        "status": "completed" if released else "error",
        "violations": [] if released else [{"code": "RESIDENCY_RETAINED",
                                            "detail": f"{held} private pages held after revocation"}],
        "validators": [],
        "timestamps": {"issued_at": done},
    }
    env["envelope_hash"] = canon.digest({k: v for k, v in env.items()
                                         if k not in ("envelope_hash", "previous_envelope_hash", "signatures")})
    env["signatures"] = [{"signer": f"{issuer}#{kid}", "kid": kid, "scheme": "Ed25519",
                          "value": canon.b64u(sk.sign(env["envelope_hash"].encode("ascii"))),
                          "signed_at": done}]
    return env


class Journal:
    """Append-only, hash-chained, fsynced revocation journal (the runtime's).

    With background=True, `submit` hands the measured facts to a writer
    thread that builds, signs, appends and fsyncs the record, so the request
    loop only pays for the measurement. `close` drains the queue and
    re-raises any writer failure."""

    def __init__(self, path, key_path, custody: dict, background: bool = False) -> None:
        self.path = pathlib.Path(path)
        self.sk, self.kid = load_key(key_path)
        self.custody = custody
        self.seq, self.prev = 0, "genesis"
        self.f = open(self.path, "ab")
        self.q, self.error, self.thread = None, None, None
        if background:
            self.q = queue.Queue()
            self.thread = threading.Thread(target=self._writer, daemon=True)
            self.thread.start()

    def _writer(self) -> None:
        while (args := self.q.get()) is not None:
            if self.error is None:
                try:
                    self.record(*args)
                except Exception as e:                  # surfaced by close()
                    self.error = e

    def submit(self, *args) -> None:
        if self.q is None:
            self.record(*args)
        else:
            self.q.put(args)

    def record(self, tenant: int, authority: dict, window, ended_at: float, started_at: float,
               completed_at: float, receipt, held_privately: int, region_pages: int,
               page_cache_resident, image_name: str) -> dict:
        body = {
            "authority": {"digest": canon.digest(authority), "class": authority.get("class"),
                          "window": list(window), "ended_at": canon.format_time(ended_at)},
            "revocation": {"started_at": canon.format_time(started_at),
                           "completed_at": canon.format_time(completed_at)},
            "receipt": {"pages": receipt.pages, "runs": [list(r) for r in receipt.runs],
                        "digest": receipt.digest},
            "image": {"name": image_name},
            "post_condition": {"held_privately": held_privately, "region_pages": region_pages,
                               "measured_by": "pagemap",
                               "page_cache_resident": page_cache_resident},
        }
        body["envelope"] = envelope(self.seq, tenant, body, self.custody, self.sk, self.kid)
        rec = {"protocol": JOURNAL_PROTOCOL, "seq": self.seq, "prev": self.prev, "kind": "REVOKED",
               "at": body["revocation"]["completed_at"], "tenant": tenant, "body": body}
        rec["hash"] = canon.digest(rec)
        self.f.write(canon.jcs(rec) + b"\n")
        self.f.flush()
        os.fsync(self.f.fileno())
        self.seq, self.prev = self.seq + 1, rec["hash"]
        return rec

    def close(self) -> None:
        if self.thread is not None:
            self.q.put(None)
            self.thread.join()
        self.f.close()
        if self.error is not None:
            raise self.error


def new_issuer(key_path) -> dict:
    """Create the runtime's issuer key; returns its public trust entry."""
    sk = Ed25519PrivateKey.generate()
    kid = save_key(pathlib.Path(key_path), sk)
    return {kid: canon.b64u(canon.public_raw(sk))}


def read_journal(path) -> list[dict]:
    data = pathlib.Path(path).read_bytes()
    if data and not data.endswith(b"\n"):
        raise WitnessRefused("E_TORN_JOURNAL", "journal does not end with a complete record")
    return [json.loads(line) for line in data.splitlines()]


class WitnessRefused(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def _atomic_write(path: pathlib.Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Witness:
    """Countersigns revocation-journal heads, only forward (W1 profile)."""

    def __init__(self, wdir) -> None:
        self.dir = pathlib.Path(wdir)
        self.sk, self.kid = load_key(self.dir / "witness.key")
        self.issuer_keys = json.loads((self.dir / "issuer_keys.json").read_text())
        self.state = json.loads((self.dir / "state.json").read_text())

    @classmethod
    def create(cls, wdir, issuer_keys: dict) -> dict:
        wdir = pathlib.Path(wdir)
        wdir.mkdir(parents=True)
        sk = Ed25519PrivateKey.generate()
        kid = save_key(wdir / "witness.key", sk)
        (wdir / "issuer_keys.json").write_text(json.dumps(issuer_keys))
        _atomic_write(wdir / "state.json", {"genesis_hash": None, "seq": None, "head_hash": None})
        return {kid: canon.b64u(canon.public_raw(sk))}

    def _fork(self, records, reason, detail):
        st = self.state
        evidence = canon.sign({
            "protocol": FORK_PROTOCOL, "reason": reason, "detail": detail,
            "witnessed": dict(st),
            "presented": {"genesis_hash": records[0]["hash"] if records else None, "length": len(records),
                          "hash_at_witnessed_seq": records[st["seq"]]["hash"] if st["seq"] < len(records) else None},
        }, self.sk, self.kid)
        forks = self.dir / "forks"
        forks.mkdir(exist_ok=True)
        (forks / f"fork-{len(list(forks.iterdir())):04d}.json").write_text(json.dumps(evidence, indent=1))
        raise WitnessRefused(reason, detail)

    def observe(self, journal_path, images_dir=None, at: str | None = None) -> dict:
        """Countersign the journal head. With `images_dir`, also hash every
        image file and include the hashes, attesting the final state."""
        records = read_journal(journal_path)
        if not records:
            raise WitnessRefused("E_EMPTY", "nothing to witness")
        verdict = verify_evidence.verify_bundle({"protocol": BUNDLE_PROTOCOL, "journal": records},
                                                {"issuer_keys": self.issuer_keys})
        st = self.state
        if st["seq"] is not None:
            if records[0]["hash"] != st["genesis_hash"]:
                self._fork(records, "E_DIFFERENT_JOURNAL", "genesis record differs")
            if len(records) <= st["seq"]:
                self._fork(records, "E_ROLLBACK", f"journal shorter than witnessed seq {st['seq']}")
            if records[st["seq"]]["hash"] != st["head_hash"]:
                self._fork(records, "E_FORK", f"record {st['seq']} differs from the witnessed head")
        if verdict["verdict"] != "VALID":
            raise WitnessRefused("E_INVALID_RECORD", f"{verdict['verdict']} ({verdict['detail']})")
        head = records[-1]
        payload = {
            "protocol": CHECKPOINT_PROTOCOL, "journal_genesis_hash": records[0]["hash"],
            "seq": head["seq"], "head_hash": head["hash"],
            "witnessed_at": at or canon.format_time(time.time()), "witness_kid": self.kid,
        }
        if images_dir is not None:
            payload["images"] = {p.name: file_sha256(p) for p in sorted(pathlib.Path(images_dir).iterdir())}
        checkpoint = canon.sign(payload, self.sk, self.kid)
        with open(self.dir / "checkpoints.jsonl", "ab") as f:
            f.write(canon.jcs(checkpoint) + b"\n")
            f.flush()
            os.fsync(f.fileno())
        self.state = {"genesis_hash": records[0]["hash"], "seq": head["seq"], "head_hash": head["hash"]}
        _atomic_write(self.dir / "state.json", self.state)
        return checkpoint


def bundle(records: list[dict], checkpoint: dict | None) -> dict:
    out = {"protocol": BUNDLE_PROTOCOL, "journal": records}
    if checkpoint is not None:
        out["witness"] = {"checkpoint": checkpoint}
    return out
