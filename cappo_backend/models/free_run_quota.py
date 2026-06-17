"""FreeRunQuota — per-workspace free run entitlement.

Every workspace gets exactly 1 free execution run.
After that, PaymentGate raises PaymentRequiredError (HTTP 402) and the
caller must pay via x402 (USDC on Base mainnet).

Reset: daily at UTC midnight (reset_at column).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tomorrow() -> datetime:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


FREE_RUN_LIMIT = 1  # One free run per workspace per day, then x402 required


class FreeRunQuota(Base):
    __tablename__ = "free_run_quotas"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True)
    runs_used: Mapped[int] = mapped_column(Integer, default=0)
    quota_limit: Mapped[int] = mapped_column(Integer, default=FREE_RUN_LIMIT)
    reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_tomorrow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
