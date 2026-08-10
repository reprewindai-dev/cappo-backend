"""Durable capability-mount lifecycle state."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityMount(Base):
    __tablename__ = "capability_mounts"

    mount_id: Mapped[str] = mapped_column(String, primary_key=True)
    token_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    token_nonce: Mapped[str] = mapped_column(String, unique=True, index=True)
    owner_principal: Mapped[str] = mapped_column(String, default="legacy-unbound", index=True)
    owner_workspace: Mapped[str] = mapped_column(String, default="legacy-unbound", index=True)
    mount_json: Mapped[dict] = mapped_column(JSON)
    token_json: Mapped[dict] = mapped_column(JSON)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    terminated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    nonce_consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    anchor_status: Mapped[str] = mapped_column(String, default="not_applicable")
    anchor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    anchor_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
