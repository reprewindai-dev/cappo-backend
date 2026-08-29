from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, Numeric, String, Column
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class X402ConsumedPayment(Base):
    __tablename__ = "x402_consumed_payments"

    tx_hash: Mapped[str] = mapped_column(String, primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String, index=True)
    endpoint: Mapped[str] = mapped_column(String, index=True)
    amount_usdc: Mapped[str] = mapped_column(String)
    chain_id: Mapped[str] = mapped_column(String, default="base")
    
    # N8N-16 Strong Settlement Identity Binding
    execution_id: Mapped[str] = mapped_column(String, unique=True, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
