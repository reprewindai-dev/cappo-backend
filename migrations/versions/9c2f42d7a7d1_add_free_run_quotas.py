"""add free run quotas table

Revision ID: 9c2f42d7a7d1
Revises: f33e7a6b27ef
Create Date: 2026-07-09 14:16:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c2f42d7a7d1"
down_revision: Union[str, None] = "f33e7a6b27ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "free_run_quotas",
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("runs_used", sa.Integer(), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("free_run_quotas")
