"""One row per consequence a target redeemed with CAPPO before committing.

The primary key on operation_id makes a consequence permit single-use: a second
redemption of the same operation fails at the database, whoever presents it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConsequenceRedemption(Base):
    __tablename__ = "consequence_redemptions"

    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    mount_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    receipt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sink_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    redeemed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
