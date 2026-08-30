"""add durable per-path runtime ownership fencing

Revision ID: c4a1f52d1b0e
Revises: a31f8c6e2d12
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a1f52d1b0e"
down_revision: Union[str, None] = "a31f8c6e2d12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_path_assignments",
        sa.Column("path_id", sa.String(), nullable=False),
        sa.Column("assignment_id", sa.String(), nullable=False),
        sa.Column("authority_epoch", sa.Integer(), nullable=False),
        sa.Column("runtime_kind", sa.String(), nullable=False),
        sa.Column("runtime_instance", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("assignment_id"),
        sa.UniqueConstraint(
            "path_id",
            "authority_epoch",
            name="uq_runtime_path_assignment_epoch",
        ),
    )
    with op.batch_alter_table("runtime_path_assignments") as batch_op:
        batch_op.create_index(
            "ix_runtime_path_assignments_path_id",
            ["path_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("runtime_path_assignments")
