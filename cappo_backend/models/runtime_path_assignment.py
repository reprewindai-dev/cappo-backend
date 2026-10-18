"""Durable ownership fence for consequence-bearing execution paths."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RuntimePathAssignment(Base):
    """The sole runtime owner of one path for one authority epoch."""

    __tablename__ = "runtime_path_assignments"
    __table_args__ = (
        UniqueConstraint(
            "path_id",
            "authority_epoch",
            name="uq_runtime_path_assignment_epoch",
        ),
    )

    assignment_id: Mapped[str] = mapped_column(String, primary_key=True)
    path_id: Mapped[str] = mapped_column(String, index=True)
    authority_epoch: Mapped[int] = mapped_column(Integer)
    runtime_kind: Mapped[str] = mapped_column(String)
    runtime_instance: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
