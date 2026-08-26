"""CapabilityActionReceipt — durable execution receipt for a completed ALLOW action.

Written exactly once per successful action evaluation. This is distinct from:
- capability_mounts: mount lifecycle state (nonce_consumed flag lives here)
- capability_evidence_consumptions: replay-prevention for approval/suppression JWTs
- the in-memory anchor spy: process-local, rebuilt on every DB load

This table proves an action occurred and completed, binding all fields required
for G0A.5 execution evidence.

Integrity columns:
- content_hash: sha256_json over canonical receipt fields (execution_id, mount_id,
  token_id, principal, action, decision, reason, actioned_at). Enables independent
  tamper detection — recompute from fields and compare.
- pgl_anchor_id: the AuditEvent.log_hash that covered this action_decision, binding
  the receipt into the existing hash-chained audit ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, LargeBinary, String
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
    decision: Mapped[str] = mapped_column(String)      # always "allow" for receipts
    reason: Mapped[str] = mapped_column(String)
    actioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # SPIFFE Identity Binding
    caller_spiffe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    executor_spiffe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    caller_cert_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    capability_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trust_domain: Mapped[str | None] = mapped_column(String, nullable=True)
    svid_not_before: Mapped[str | None] = mapped_column(String, nullable=True)
    svid_not_after: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String, nullable=True)
    biscuit_token_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    signed_receipt_cose: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # Integrity: sha256_json over canonical receipt fields (excluding created_at).
    # Recompute and compare to detect silent field mutation.
    content_hash: Mapped[str] = mapped_column(String)

    # PGL chain binding: the AuditEvent.log_hash written at the same action_decision.
    # In tests (UnconfirmedAnchor) this is the spy's sequential anchor-N id.
    # In production (AuditPGLAnchor) this is the sha256-based log_hash.
    pgl_anchor_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

