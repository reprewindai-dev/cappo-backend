"""ExecutionIdentity — persistent record of a minted ExecutionIdentityV1.

Columns follow the ``execution_identities`` table suggested in the EI
Implementation Plan (§Persistence model). The full canonical object (all EI
fields) is stored in ``identity_json``; the hot-path columns the gateway needs
for validation/revocation are denormalised for indexed lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionIdentity(Base):
    __tablename__ = "execution_identities"

    execution_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    workspace_id: Mapped[str] = mapped_column(String, index=True)

    pgl_pre_certificate_id: Mapped[str] = mapped_column(String, index=True)
    pgl_post_certificate_id: Mapped[str | None] = mapped_column(String, nullable=True)

    directive: Mapped[str] = mapped_column(String)
    risk_tier: Mapped[str] = mapped_column(String)
    budget_approved_cents: Mapped[int] = mapped_column(Integer, default=0)
    delegation_depth: Mapped[int] = mapped_column(Integer, default=0)

    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    signature: Mapped[str] = mapped_column(String)
    hash: Mapped[str] = mapped_column(String)

    # Full canonical ExecutionIdentityV1 object.
    identity_json: Mapped[dict] = mapped_column(JSON, default=dict)

    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
