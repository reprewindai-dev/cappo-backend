"""ExecutionAuthorization — persistent record of a minted EAT.

Columns follow the ``execution_authorizations`` table.  The full canonical EAT
object is stored in ``eat_json``; hot-path columns the edge gateway needs for
validation, consumption, and revocation are denormalised for indexed lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionAuthorization(Base):
    __tablename__ = "execution_authorizations"

    eat_id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_id: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)

    directive: Mapped[str] = mapped_column(String)
    risk_tier: Mapped[str] = mapped_column(String)
    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    nonce: Mapped[str] = mapped_column(String, index=True)
    signature: Mapped[str] = mapped_column(String)
    hash: Mapped[str] = mapped_column(String)

    # Full canonical EAT object.
    eat_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
