"""KillSwitch — per-workspace execution kill switch.

Lineage seed: the old backend's Redis ``kill_switch:{workspace_id}`` flag
(migration note §7). When active, the payment gate returns HTTP 402 and that
response **takes precedence** over the LAW 0 403 (EI Plan §Priority rule). Stored
durably (one row per workspace) rather than only in Redis.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KillSwitch(Base):
    __tablename__ = "kill_switches"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
