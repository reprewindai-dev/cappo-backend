"""WorkspaceBudget — per-workspace spendable balance with exactly-once hold semantics.

Lineage seed: the old backend's budget/kill-switch 402 path (migration note §7).
When a row exists and the action cost exceeds ``balance_cents - holds``, the payment gate
returns HTTP 402. Absence of a row means unmetered.
"""

from __future__ import annotations

from datetime import datetime, timezone
import enum

from sqlalchemy import DateTime, Integer, String, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)

class HoldStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"

class WorkspaceBudget(Base):
    __tablename__ = "workspace_budgets"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

class WorkspaceBudgetHold(Base):
    """
    Transactionally sound outbox/ledger constraint for reservations.
    Ties pre-flight holds strictly to a unique execution_id.
    """
    __tablename__ = "workspace_budget_holds"

    execution_id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, ForeignKey("workspace_budgets.workspace_id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[HoldStatus] = mapped_column(SQLEnum(HoldStatus), nullable=False, default=HoldStatus.ACTIVE)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
