"""PGLLedgerEvent — hash-chained certificate lifecycle ledger.

Lineage seed: ``AIAuditLog.previous_log_hash`` chaining (migration note §6). Each
event references the previous event's ``event_hash`` to form a tamper-evident
chain over certificate lifecycle transitions (minted, attested, revoked, ...).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PGLLedgerEvent(Base):
    __tablename__ = "pgl_ledger_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    certificate_id: Mapped[str] = mapped_column(String, index=True)
    event_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    previous_event_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    event_hash: Mapped[str] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
