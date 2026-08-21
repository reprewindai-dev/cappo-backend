"""cAPI gatekeeper primitives for governed CAPPO execution.

The exec route uses this module after RFC 9421 message verification to create
deterministic request evidence and validate the existing cAPI security
envelope. This module is not the public protocol gate and does not grant
authority; CAPPO's governed execution pipeline retains that responsibility.

Durability boundary
───────────────────
This module creates canonical request/result seals only. It does not claim to
persist them: CAPPO binds a resulting seal to the attested PGL certificate via
``RunOrchestrator.record_evidence_seal`` inside the governed lifecycle. That
keeps the durable evidence record in the canonical PGL chain rather than in an
unmigrated side table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cappo_backend.services.canonical import sha256_json, verify_signature_ed25519


class CAPIPipelineError(ValueError):
    """Raised when a cAPI gatekeeper validation rule rejects the request."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_security_payload(actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "action": payload.get("action"),
        "data_hash": sha256_json(payload.get("data") or {}),
        "nonce": (payload.get("security") or {}).get("nonce"),
    }


async def enforce_capi_pipeline(
    actor_id: str,
    payload: dict[str, Any],
    public_key: str | bytes,
) -> dict[str, Any]:
    """Validate an execution intent and return a deterministic evidence handle.

    Security envelope rules:
      - ``security`` is optional for internal authenticated execution.
      - when present, ``nonce`` and ``signature`` must both be present.
      - the signature must verify over actor/action/data-hash/nonce.
    """
    if not actor_id:
        raise CAPIPipelineError("actor_id is required")
    if not isinstance(payload, dict):
        raise CAPIPipelineError("payload must be an object")

    action = payload.get("action")
    data = payload.get("data")
    if not isinstance(action, str) or not action:
        raise CAPIPipelineError("action is required")
    if not isinstance(data, dict):
        raise CAPIPipelineError("data must be an object")

    security = payload.get("security")
    signature_validated = False
    if security is not None:
        if not isinstance(security, dict):
            raise CAPIPipelineError("security must be an object")
        nonce = security.get("nonce")
        signature = security.get("signature")
        if not nonce or not isinstance(nonce, str):
            raise CAPIPipelineError("security.nonce is required")
        if not signature or not isinstance(signature, str):
            raise CAPIPipelineError("security.signature is required")

        signed_payload = _canonical_security_payload(actor_id, payload)
        if not verify_signature_ed25519(signed_payload, signature, public_key):
            raise CAPIPipelineError("security.signature verification failed")
        signature_validated = True

    evidence = {
        "actor_id": actor_id,
        "action": action,
        "data_hash": sha256_json(data),
        "security_hash": sha256_json(security or {}),
        "signature_validated": signature_validated,
        "issued_at": _now_iso(),
        "pipeline": "capi-gatekeeper-v1",
        "phases": [
            "intake",
            "canonicalize",
            "security-envelope",
            "policy-preflight",
            "evidence-commit",
        ],
    }
    evidence_id = sha256_json({k: v for k, v in evidence.items() if k != "issued_at"})
    return {
        "status": "accepted",
        "evidence_id": evidence_id,
        "evidence_hash": evidence_id,
        "evidence": evidence,
    }


def _governed_compute_summary(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract only durable, non-secret consequence facts for the PGL seal."""

    if result.get("provider") != "lockerphycer-governed-cell":
        return None
    cell = result.get("governed_cell")
    effect = result.get("effect")
    if not isinstance(cell, dict) or not isinstance(effect, dict):
        return None

    cell_fields = (
        "cell_id",
        "runtime",
        "authority_digest",
        "started_at",
        "completed_at",
        "network_mode",
        "credential_mode",
        "teardown_confirmed",
    )
    effect_fields = (
        "provider",
        "operation",
        "repository",
        "branch",
        "path",
        "before_sha",
        "after_blob_sha",
        "commit_sha",
        "effect_digest",
        "mutation_succeeded",
        "credential_revoked",
        "security_status",
    )
    return {
        "profile": "veklom-governed-compute-p0",
        "cell": {key: cell.get(key) for key in cell_fields},
        "effect": {key: effect.get(key) for key in effect_fields},
        "security_status": result.get("security_status"),
        "credential_revocation_confirmed": result.get("credential_revocation_confirmed"),
    }


async def seal_evidence_pack(
    evidence_id: str,
    result: dict[str, Any],
    *,
    request_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the post-execution seal that CAPPO will append to PGL.

    Raw request/result payloads remain excluded. For a governed-cell consequence,
    a narrow allowlisted summary of the physical cell and resulting target state
    is included so the PGL event preserves more than an opaque result hash.
    Persistence happens through the existing PGL certificate ledger.
    """
    if not evidence_id:
        raise CAPIPipelineError("evidence_id is required")
    if not isinstance(result, dict):
        raise CAPIPipelineError("result must be an object")

    seal = {
        "evidence_id": evidence_id,
        "result_hash": sha256_json(result),
        "sealed_at": _now_iso(),
        "seal_version": "capi-evidence-seal-v1",
    }
    if request_evidence is not None:
        seal["request_evidence"] = request_evidence
    governed_summary = _governed_compute_summary(result)
    if governed_summary is not None:
        seal["governed_compute"] = governed_summary
    seal["seal_hash"] = sha256_json({k: v for k, v in seal.items() if k != "sealed_at"})
    return seal
