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

from sqlalchemy import BigInteger, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityActionReceipt(Base):
    __tablename__ = "capability_action_receipts"

    receipt_id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_id: Mapped[str] = mapped_column(String, index=True)
    mount_id: Mapped[str] = mapped_column(String, index=True)
    token_id: Mapped[str] = mapped_column(String, index=True)
    principal: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    resource: Mapped[str | None] = mapped_column(String, nullable=True)
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)



