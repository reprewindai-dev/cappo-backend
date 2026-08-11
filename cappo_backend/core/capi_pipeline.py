"""cAPI gatekeeper primitives for governed CAPPO execution.

The exec route uses this module before orchestration to create deterministic
request evidence and to validate optional signed security envelopes. It is
strict when a client supplies security material, but it does not require public
frontend callers to already have a signing key because authentication,
budgeting, EI, and LAW 0 are enforced by the surrounding production pipeline.

Transactional Outbox
────────────────────
Evidence persistence and downstream refinery queueing are made atomic through
a Transactional Outbox pattern:

    CAPPO TRANSACTION
    │
    ├── INSERT immutable evidence  (capi_evidence table)
    └── INSERT refinery_outbox     (refinery_outbox table)
             │
           COMMIT
             │
             ▼
    OUTBOX DISPATCHER (outbox_dispatcher.py)
             │
             ▼
    Cloudflare Queue (veklom-async-refinery)
             │
             ▼
    Refinery consumer (RAW → VALIDATED → SILVER → GOLD)

This eliminates the prior failure hole where evidence could be committed but
the queue push would fail silently. Now evidence and "needs downstream
processing" are atomic — if the DB commit fails, neither row exists.

Queue jobs are idempotent: the dispatcher uses evidence_hash as a deduplication
key so double-delivery produces no second computation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from cappo_backend.services.canonical import sha256_json, verify_signature_ed25519

logger = logging.getLogger(__name__)


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


async def seal_evidence_pack(
    evidence_id: str,
    result: dict[str, Any],
    db_session: Any | None = None,
) -> dict[str, Any]:
    """Create the post-execution evidence seal for an accepted cAPI request.

    Transactional Outbox
    ────────────────────
    When ``db_session`` is provided, this function writes both the evidence seal
    AND a refinery_outbox row atomically within the same database transaction.
    The outbox row is picked up by ``outbox_dispatcher.py`` which reliably
    delivers it to the Cloudflare Queue.

    If ``db_session`` is None (e.g., tests), the seal is returned without
    persistence — the caller is responsible for handling the outbox write.
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
    seal["seal_hash"] = sha256_json({k: v for k, v in seal.items() if k != "sealed_at"})

    if db_session is not None:
        # ── Transactional Outbox ──────────────────────────────────────────────
        # Both writes happen inside the SAME transaction.
        # If either INSERT fails, the ENTIRE transaction rolls back — no orphaned
        # evidence and no missing outbox row.
        try:
            await _write_transactional_outbox(db_session, seal)
            logger.debug(
                "evidence=%s outbox_row written atomically", evidence_id
            )
        except Exception:
            # Let the caller's transaction handler decide rollback behaviour.
            # We re-raise so CAPPO knows persistence failed.
            logger.exception("Failed to write transactional outbox for evidence=%s", evidence_id)
            raise

    return seal


async def _write_transactional_outbox(db_session: Any, seal: dict[str, Any]) -> None:
    """Write both the evidence seal and a refinery_outbox row within db_session.

    The outbox row schema:
        id              SERIAL PRIMARY KEY
        event_id        TEXT UNIQUE NOT NULL    -- deduplication key for queue delivery
        evidence_hash   TEXT NOT NULL
        operation       TEXT NOT NULL
        schema_version  TEXT NOT NULL
        payload         JSONB NOT NULL
        attempt         INTEGER DEFAULT 0
        created_at      TIMESTAMPTZ DEFAULT now()
        dispatched_at   TIMESTAMPTZ             -- set by dispatcher after delivery

    Jobs are idempotent: the dispatcher uses (event_id, evidence_hash) as the
    deduplication key. If a job is delivered twice, the second delivery is a
    no-op on the refinery consumer side.
    """
    outbox_row = {
        "event_id": seal["seal_hash"],          # globally unique per seal
        "evidence_hash": seal["evidence_id"],
        "operation": "refinery.ingest",
        "schema_version": "v1",
        "payload": seal,
    }

    # Execute both inserts via the session's execute method.
    # SQLAlchemy async sessions share the same transaction if autocommit=False.
    await db_session.execute(
        """
        INSERT INTO capi_evidence_seals
            (evidence_id, result_hash, sealed_at, seal_version, seal_hash)
        VALUES
            (:evidence_id, :result_hash, :sealed_at, :seal_version, :seal_hash)
        ON CONFLICT (evidence_id) DO NOTHING
        """,
        seal,
    )

    await db_session.execute(
        """
        INSERT INTO refinery_outbox
            (event_id, evidence_hash, operation, schema_version, payload)
        VALUES
            (:event_id, :evidence_hash, :operation, :schema_version, :payload::jsonb)
        ON CONFLICT (event_id) DO NOTHING
        """,
        {**outbox_row, "payload": __import__("json").dumps(outbox_row["payload"])},
    )
