"""EEE v0.1.0 envelopes for VDB-C1 outcomes (EEE-Core; no chaining).

One envelope per terminal attempt, derived purely from the journal, so the
same journal always yields byte-identical envelopes (Ed25519 is
deterministic):

  FINALIZED              -> status "completed", actual_effects = [the upsert]
  ADMISSION_REJECTED     -> status "denied"   (denials are evidence, EEE §6)
  PRECONDITION_REJECTED  -> status "denied"
  ABORTED                -> status "error",   actual_effects = []

COMMIT_UNCERTAIN is not terminal and gets no envelope until recovery
resolves it.

Wire format, written independently and matched to CAPPO's
cappo_backend/services/eee.py:
- envelope_hash = "sha256:" + hex(SHA-256(JCS(envelope minus envelope_hash,
  previous_envelope_hash, signatures)));
- the signature is Ed25519 over the ASCII envelope_hash, base64url without
  padding;
- signer = issuer + "#" + kid.

Revocation: C1 intents are single-use and expire within 10 minutes; C1 has
no revocation list. The envelope says so with method "none", which
verifiers must report as REVOCATION_NOT_CHECKED (EEE §3.4, HR-006).
"""

from __future__ import annotations

import hashlib
import pathlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vdb_c1 import canon

EEE_VERSION = "0.1.0"
CAPABILITY_ID = "vdb.c1.upsert"
POLICY_BUNDLE_ID = "vdb-c1-admission-v1"
POLICY_RULES = ["schema.exact", "protocol", "op.upsert", "namespace", "kid.match",
                "key.trusted", "signature", "validity.window", "ttl.max", "precondition.generation",
                "precondition.root", "commit.fresh_read"]
TERMINAL = ("FINALIZED", "ADMISSION_REJECTED", "PRECONDITION_REJECTED", "ABORTED")
HERE = pathlib.Path(__file__).resolve().parent


def enforcer() -> dict:
    h = hashlib.sha256()
    for name in ("coordinator.py", "state_adapter.py", "journal.py", "canon.py", "states.py"):
        h.update((HERE / name).read_bytes())
    return {"name": "vdb_c1", "version": "1.0.0", "build_hash": "sha256:" + h.hexdigest()}


def _root(envelope: dict) -> str:
    body = {k: v for k, v in envelope.items()
            if k not in ("envelope_hash", "previous_envelope_hash", "signatures")}
    return canon.digest(body)


def _capability(namespace: str) -> dict:
    return {"capability_id": CAPABILITY_ID, "op": "UPSERT", "namespace": namespace,
            "key_max": 256, "value_max": 65536}


def _decision(gate, rule, decision, at, reason):
    return {"gate": gate, "rule_id": rule, "decision": decision, "evaluated_at": at,
            "latency_ms": 0, "reason_code": reason}


def envelope_for(records: list[dict], terminal: dict, namespace: str, store_id: str,
                 sk: Ed25519PrivateKey, kid: str) -> dict:
    """Build and sign the envelope for one terminal journal record."""
    iid, kind, at = terminal["intent_id"], terminal["kind"], terminal["at"]
    binding = [r for r in records if r["intent_id"] == iid and r["seq"] <= terminal["seq"]
               and r["kind"] in ("PREPARED", "COMMITTING", "COMMIT_UNCERTAIN", "COMMITTED",
                                 "FINALIZED", "ABORTED")]
    prepared = binding[0] if binding and binding[0]["kind"] == "PREPARED" else None
    # Admitted intents (PREPARED, or a precondition rejection after admission)
    # carry their verified intent. An admission rejection established no
    # authority, so its window is the zero-length window at the decision.
    if prepared:
        intent = prepared["body"]["signed_intent"]["payload"]
    elif kind == "PRECONDITION_REJECTED":
        intent = terminal["body"]["signed_intent"]["payload"]
    else:
        intent = None
    issuer = f"urn:veklom:vdb-c1:{store_id}"
    upsert = f"state:upsert:{namespace}"
    reason = terminal["body"].get("reason", kind)

    if kind == "ADMISSION_REJECTED":
        started, decisions = at, [_decision("admission", "admission", "deny", at, reason)]
    elif kind == "PRECONDITION_REJECTED":
        started = at
        decisions = [_decision("admission", "admission", "allow", at, "ADMITTED"),
                     _decision("precondition", "precondition", "deny", at, reason)]
    else:
        started = prepared["at"]
        decisions = [_decision("admission", "admission", "allow", started, "ADMITTED"),
                     _decision("precondition", "precondition", "allow", started, "PRECONDITIONS_MET"),
                     _decision("commit", "commit.fresh_read",
                               "allow" if kind == "FINALIZED" else "deny", at, reason)]

    granted = {"writes": 1}
    consumed = {"writes": 1 if kind == "FINALIZED" else 0}
    if kind == "FINALIZED":
        committed = next(r for r in binding if r["kind"] == "COMMITTED")
        output = committed["body"]["post_root"]
    else:
        output = canon.digest({"protocol": "veklom.vdb.c1.outcome/v1", "intent_id": iid,
                               "outcome": kind, "reason": reason})
    authority_kid = intent["authority_kid"] if intent else "unadmitted"
    issued_at = intent["issued_at"] if intent else at
    expires_at = intent["expires_at"] if intent else at
    env = {
        "eee_version": EEE_VERSION,
        "hash_alg": "SHA-256",
        "execution_id": iid if kind in ("FINALIZED", "ABORTED") else f"{iid}#{terminal['seq']}",
        "idempotency_key": iid,
        "issuer": issuer,
        "enforcer": enforcer(),
        "participant_identity": {"scheme": "ed25519-kid", "identifier": authority_kid},
        "principal": {"scheme": "ed25519-kid", "identifier": authority_kid},
        "capability_id": CAPABILITY_ID,
        "capability_hash": canon.digest(_capability(namespace)),
        "capability_attenuation": {"resource_allowlist": [namespace],
                                   "argument_constraints": {"key_max": 256, "value_max": 65536},
                                   "delegation_depth": 0, "delegation_depth_max": 0},
        "runtime_lineage": {"model": "none", "model_version": "none", "framework": "vdb_c1",
                            "framework_version": "1.0.0",
                            "config_hash": canon.digest({"adapter": "sqlite-wal/v1",
                                                         "journal_mode": "wal",
                                                         "synchronous": "FULL"})},
        "authority_chain": [{"type": "capability-grant",
                             "artifact_hash": terminal["body"].get("intent_hash") or canon.digest(None),
                             "issuer": authority_kid, "granted_at": issued_at,
                             "expires_at": expires_at}],
        "authority_window": {"not_before": issued_at, "not_after": expires_at},
        "revocation_check": {"method": "none", "checked_at": started,
                             "result": "single-use, expiring intent; no revocation list"},
        "policy_bundle_id": POLICY_BUNDLE_ID,
        "policy_hash": canon.digest({"bundle_id": POLICY_BUNDLE_ID, "rules": POLICY_RULES}),
        "policy_decisions": decisions,
        "enforcement_mode": "fail-closed",
        "input_commitment": (prepared["body"]["payload_commitment"] if prepared
                             else terminal["body"].get("intent_hash") or canon.digest(None)),
        "effect_grammar": "EEE-EFFECTS-1",
        "allowed_effects": [upsert] if kind != "ADMISSION_REJECTED" else [],
        "actual_effects": [upsert] if kind == "FINALIZED" else [],
        "tool_actions": [{"tool": "sqlite-wal", "action_hash": prepared["body"]["payload_commitment"],
                          "decision": "committed" if kind == "FINALIZED" else "not_committed",
                          "evidence_ref": f"journal:{terminal['seq']}:{terminal['hash']}"}]
                        if prepared else [],
        "budget": {"granted": granted, "consumed": consumed},
        "started_at": started,
        "ended_at": at,
        "output_commitment": output,
        "status": {"FINALIZED": "completed", "ABORTED": "error"}.get(kind, "denied"),
        "violations": [],
        "validators": [],
        "timestamps": {"issued_at": at},
    }
    env["envelope_hash"] = _root(env)
    env["signatures"] = [{
        "signer": f"{issuer}#{kid}", "kid": kid, "scheme": "Ed25519",
        "value": canon.b64u(sk.sign(env["envelope_hash"].encode("ascii"))),
        "signed_at": at,
    }]
    return env


def envelopes_from_journal(records: list[dict], namespace: str, store_id: str,
                           sk: Ed25519PrivateKey, kid: str) -> list[dict]:
    return [envelope_for(records, r, namespace, store_id, sk, kid)
            for r in records if r["kind"] in TERMINAL]
