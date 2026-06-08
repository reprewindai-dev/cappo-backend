"""WorkspaceBudget — per-workspace spendable balance.

Lineage seed: the old backend's budget/kill-switch 402 path (migration note §7).
When a row exists and the action cost exceeds ``balance_cents``, the payment gate
returns HTTP 402 (which precedes the LAW 0 403). Absence of a row means the
workspace is unmetered (development convenience); production seeds balances
explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkspaceBudget(Base):
    __tablename__ = "workspace_budgets"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
