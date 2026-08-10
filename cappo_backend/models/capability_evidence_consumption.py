"""Consumed approval/suppression evidence identifiers for replay prevention."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityEvidenceConsumption(Base):
    __tablename__ = "capability_evidence_consumptions"

    jti: Mapped[str] = mapped_column(String(255), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mount_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
