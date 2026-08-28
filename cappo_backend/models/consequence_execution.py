"""ConsequenceExecutionEvent — durable, append-only consequence lifecycle record.

SEPARATE from CapabilityActionReceipt (which is authorization evidence).
CapabilityActionReceipt proves: CAPPO said yes.
ConsequenceExecutionEvent proves: what actually happened as a result.

Event-Sourced State Machine:
    Each state transition is a NEW ROW (append-only evidence).
    We never mutate historical evidence.

    E001: AUTHORIZED (written before fn() call, bound to receipt_id)
    E002: STARTED    (written atomically before fn() executes)
    E003: SUCCEEDED | FAILED | OUTCOME_UNKNOWN (written after fn() settles)
    E004: RECONCILED_SUCCEEDED | RECONCILED_FAILED (if OUTCOME_UNKNOWN is resolved)

Idempotency:
    operation_id: opaque durable ID for this consequence attempt
    intent_hash:  sha256_json(mount_id, execution_id, action, resource, normalized_args)
    All events for the same operation_id share the same intent_hash.

Proof types:
    completion_proof_type records WHY Veklom believes the state it reports:
        "callback_return"            — callback returned without exception
        "callback_exception"         — callback raised an exception before consequence
        "reconciliation_filesystem"  — post-restart filesystem inspection
        "reconciliation_db_query"    — post-restart DB query
        "reconciliation_api_query"   — post-restart external API query
        "outcome_uncertain"          — process died; no proof available
        "optimistic_claim"           — local DB row lock acquired
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base
from cappo_backend.services.canonical import sha256_json


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConsequenceState(str, enum.Enum):
    AUTHORIZED           = "authorized"
    STARTED              = "started"
    SUCCEEDED            = "succeeded"
    FAILED               = "failed"
    OUTCOME_UNKNOWN      = "outcome_unknown"
    RECONCILED_SUCCEEDED = "reconciled_succeeded"
    RECONCILED_FAILED    = "reconciled_failed"


# Structural FSM — only these forward transitions are legal in the event stream.
_ALLOWED_TRANSITIONS: dict[ConsequenceState, set[ConsequenceState]] = {
    # If None (first event), must be AUTHORIZED. Enforced in logic.
    ConsequenceState.AUTHORIZED:      {ConsequenceState.STARTED, ConsequenceState.FAILED},
    ConsequenceState.STARTED:         {ConsequenceState.SUCCEEDED, ConsequenceState.FAILED, ConsequenceState.OUTCOME_UNKNOWN},
    ConsequenceState.OUTCOME_UNKNOWN: {ConsequenceState.RECONCILED_SUCCEEDED, ConsequenceState.RECONCILED_FAILED},
    ConsequenceState.SUCCEEDED:       set(),   # terminal
    ConsequenceState.FAILED:          set(),   # terminal
    ConsequenceState.RECONCILED_SUCCEEDED: set(),
    ConsequenceState.RECONCILED_FAILED: set(),
}


class ConsequenceInvariantViolation(Exception):
    """Raised when an illegal consequence state transition is attempted."""


class ConsequenceExecutionEvent(Base):
    """Append-only ledger of consequence lifecycle events."""

    __tablename__ = "consequence_execution_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)

    # Identifies the execution thread
    operation_id: Mapped[str] = mapped_column(String, index=True)

    # Idempotency: sha256_json over canonical intent fields.
    intent_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # State this event represents
    state: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Event order (0 = AUTHORIZED, 1 = STARTED, 2 = SUCCEEDED...)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Authorization receipt that permitted this attempt — only strictly required on AUTHORIZED.
    receipt_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Identity / scope fields
    mount_id: Mapped[str] = mapped_column(String, index=True)
    execution_id: Mapped[str] = mapped_column(String, index=True)
    principal: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    resource: Mapped[str | None] = mapped_column(String, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Proof and Error
    completion_proof_type: Mapped[str | None] = mapped_column(String, nullable=True)
    completion_proof_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Bound to the cryptographic evidence
    proof_subject_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("operation_id", "version", name="uq_consequence_event_op_version"),
    )


# ---------------------------------------------------------------------------
# Idempotency helper
# ---------------------------------------------------------------------------

def build_intent_hash(
    mount_id: str,
    execution_id: str,
    action: str,
    resource: str | None,
    normalized_args: dict | None = None,
) -> str:
    payload = {
        "mount_id": mount_id,
        "execution_id": execution_id,
        "action": action,
        "resource": resource or "*",
        "normalized_args": normalized_args or {},
    }
    return sha256_json(payload)

def build_proof_subject_hash(
    operation_id: str,
    intent_hash: str,
    previous_truth_state: str,
    asserted_truth_state: str,
    consequence_identity: str,
    canonical_asserted_proposition: str,
) -> str:
    payload = {
        "operation_id": operation_id,
        "intent_hash": intent_hash,
        "previous_truth_state": previous_truth_state,
        "asserted_truth_state": asserted_truth_state,
        "consequence_identity": consequence_identity,
        "canonical_asserted_proposition": canonical_asserted_proposition,
    }
    return sha256_json(payload)
