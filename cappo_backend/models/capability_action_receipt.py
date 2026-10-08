"""CapabilityActionReceipt — immutable authorization evidence.

Written exactly once when CAPPO evaluates a capability action and returns ALLOW.
This receipt proves:
    - CAPPO authorized this action
    - At this time, under this identity, with this authority

This receipt does NOT prove:
    - That the consequence was attempted
    - That the consequence succeeded
    - That the consequence was completed

Consequence lifecycle (STARTED / SUCCEEDED / FAILED / OUTCOME_UNKNOWN) is
tracked in ConsequenceExecution. These are separate semantic facts.

IMMUTABILITY RULE: This row must never be updated after the initial write.
It is append-only evidence. New facts about consequence completion must be
written as separate ConsequenceExecution events — never as mutations to this row.

Integrity columns:
- content_hash: sha256_json over canonical receipt fields. Enables independent
  tamper detection — recompute from fields and compare.
- pgl_anchor_id: the AuditEvent.log_hash that covered this action_decision.
- merkle_leaf_index: persistent monotonic append position.
"""

from __future__ import annotations

from datetime import datetime, timezone

from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base

# Receipt canonical-form versions. A row's version decides how its content_hash and
# signed COSE payload are (re)computed; historical rows are never reinterpreted.
#   v1 (NULL column): the original form, resource recorded as the placeholder "*".
#   v2: names the actual resource, target_ref and bound-operation envelope digest.
RECEIPT_SCHEMA_V1 = 1
RECEIPT_SCHEMA_V2 = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def receipt_canonical(row: "CapabilityActionReceipt") -> dict[str, Any]:
    """The canonical fields a receipt's content_hash and COSE signature cover.

    Derived only from stored columns, so any verifier can recompute it from the row.
    """
    actioned_at = row.actioned_at
    if actioned_at.tzinfo is None:
        actioned_at = actioned_at.replace(tzinfo=timezone.utc)
    version = row.receipt_schema_version or RECEIPT_SCHEMA_V1
    canonical: dict[str, Any] = {
        "execution_id": row.execution_id,
        "mount_id": row.mount_id,
        "token_id": row.token_id,
        "principal": row.principal,
        "caller_spiffe_id": row.caller_spiffe_id,
        "executor_spiffe_id": row.executor_spiffe_id,
        "eei_id": row.eei_id,
        "profile_id": row.profile_id,
        "lease_id": row.lease_id,
        "operator_id": row.operator_id,
        "caller_cert_sha256": row.caller_cert_sha256,
        "capability_id": row.capability_id,
        "biscuit_token_sha256": row.biscuit_token_sha256,
        "action": row.action,
        "resource": "*",
        "policy_version": row.policy_version,
        "decision": row.decision,
        "reason": row.reason,
        "timestamp": actioned_at.isoformat(),
        "actioned_at": actioned_at.isoformat(),
        "result_hash": None,
        "pgl_anchor_id": row.pgl_anchor_id,
    }
    if version == RECEIPT_SCHEMA_V1:
        return canonical
    if version != RECEIPT_SCHEMA_V2:
        raise ValueError(f"unknown receipt schema version {version}")
    return {
        **canonical,
        "receipt_schema_version": RECEIPT_SCHEMA_V2,
        "resource": row.resource,
        "target_ref": row.target_ref,
        "envelope_digest": row.envelope_digest,
    }


class CapabilityActionReceipt(Base):
    __tablename__ = "capability_action_receipts"

    receipt_id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_id: Mapped[str] = mapped_column(String, index=True)
    mount_id: Mapped[str] = mapped_column(String, index=True)
    token_id: Mapped[str] = mapped_column(String, index=True)
    principal: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    resource: Mapped[str | None] = mapped_column(String, nullable=True)
    # The target dispatched to, and the bound operation's envelope digest (operation-bound
    # mounts only). Both are in the signed canonical receipt, so both are stored.
    target_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    envelope_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    # NULL = v1 (historical canonical form); see receipt_canonical().
    receipt_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str] = mapped_column(String)      # always "allow" for receipts
    reason: Mapped[str] = mapped_column(String)
    actioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # SPIFFE Identity Binding
    caller_spiffe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    executor_spiffe_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Ephemeral Agent Doctrine (G0A.8)
    eei_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    lease_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    operator_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    caller_cert_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    capability_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trust_domain: Mapped[str | None] = mapped_column(String, nullable=True)
    svid_not_before: Mapped[str | None] = mapped_column(String, nullable=True)
    svid_not_after: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String, nullable=True)
    biscuit_token_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    signed_receipt_cose: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # Merkle append position — monotonically increasing. ORDER BY ASC for canonical order.
    merkle_leaf_index: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True, index=True
    )

    # Integrity: sha256_json over canonical receipt fields. Recompute to detect tampering.
    content_hash: Mapped[str] = mapped_column(String)

    # PGL chain binding.
    pgl_anchor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pgl_anchor_status: Mapped[str | None] = mapped_column(String, nullable=True)
    pgl_event_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)



