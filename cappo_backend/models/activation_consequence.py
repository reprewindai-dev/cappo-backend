"""Durable first-party consequence used by the Veklom Activation flow.

This table is deliberately separate from authorization receipts and P5 lifecycle
records. A row here is the independently observable target mutation itself.
The database uniqueness fences make an execution physically single-effect even
if a caller attempts to redeliver the same operation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ActivationConsequence(Base):
    """One durable, workspace-bound Activation marker consequence."""

    __tablename__ = "activation_consequences"

    consequence_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: f"act_{uuid.uuid4().hex}",
    )
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    mount_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    receipt_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    marker_value: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            name="uq_activation_consequence_execution_id",
        ),
        UniqueConstraint(
            "operation_id",
            name="uq_activation_consequence_operation_id",
        ),
    )
