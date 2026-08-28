"""P5 Execution-Truth Invariant -- SQLAlchemy database models.

Three tables form the P5 ledger spine:
  P5Operation  -- mutable head-of-state for each operation (one row per op)
  P5Event      -- append-only event log (never mutated)
  P5Outbox     -- transactional outbox for downstream pub/sub fanout

Deterministic ordering rule:
  P5 event chains MUST be ordered by (event_sequence ASC), never by
  created_at alone. Two events can share the same timestamp resolution.
  event_sequence is a monotonically increasing INTEGER assigned by the
  engine via MAX(event_sequence)+1 per operation. The first event in an
  operation always has event_sequence=0 and previous_event_hash=None.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class P5Operation(Base):
    """Mutable head-of-state row for a P5 operation."""

    __tablename__ = "p5_operations"

    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    consequence_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sink_class: Mapped[str] = mapped_column(String, nullable=False)
    current_truth_state: Mapped[str] = mapped_column(String, nullable=False)
    intent_hash: Mapped[str] = mapped_column(String, nullable=False)
    actor_identity: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class P5Event(Base):
    """Append-only event record -- never mutated after insertion.

    event_sequence is a deterministic, per-operation monotonically
    increasing counter (0, 1, 2, ...) assigned by the engine.
    It provides collision-proof ordering independent of timestamp.
    First event: event_sequence=0, previous_event_hash=None.
    """

    __tablename__ = "p5_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, ForeignKey("p5_operations.operation_id"), nullable=False, index=True)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    previous_event_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    previous_truth_state: Mapped[str | None] = mapped_column(String, nullable=True)
    asserted_truth_state: Mapped[str | None] = mapped_column(String, nullable=True)
    proof_type: Mapped[str | None] = mapped_column(String, nullable=True)
    proof_subject_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    cappo_decision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_hash: Mapped[str] = mapped_column(String, nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class P5Outbox(Base):
    """Transactional outbox for P5 event fanout."""

    __tablename__ = "p5_outbox"

    outbox_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, ForeignKey("p5_events.event_id"), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="PENDING", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)