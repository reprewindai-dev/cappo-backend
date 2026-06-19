"""X402ConsumedPayment — persistent replay protection.

Stores on-chain transaction hashes that have been consumed to prevent double-spending
across server restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class X402ConsumedPayment(Base):
    __tablename__ = "x402_consumed_payments"

    tx_hash: Mapped[str] = mapped_column(String, primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String, index=True)
    endpoint: Mapped[str] = mapped_column(String)
    amount_usdc: Mapped[str] = mapped_column(String)
    chain_id: Mapped[str] = mapped_column(String)
    block_number: Mapped[str | None] = mapped_column(String, nullable=True)

    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
