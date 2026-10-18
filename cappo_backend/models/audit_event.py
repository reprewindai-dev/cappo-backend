"""AuditEvent — hash-chained audit/ledger record.

Lineage seed: ``AIAuditLog`` (migration note §6). Immutable, hash-chained
(``previous_log_hash`` -> ``log_hash``) with a typed ``operation_type``
discriminator. ``law0_violation`` is a first-class operation type, written by the
MCP gateway when it rejects an execution. Unlike the old best-effort logger,
governance-critical events must be written fail-loud (see AuditService).
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


class AuditEvent(Base):
    __tablename__ = "audit_events"

    log_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    operation_type: Mapped[str] = mapped_column(String, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    previous_log_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    log_hash: Mapped[str] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
