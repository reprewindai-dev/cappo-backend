"""add the VNP performance leaderboard table

Revision ID: b7d1e4f6a902
Revises: 9978153219c2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d1e4f6a902"
down_revision: Union[str, None] = "9978153219c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vnp_performance_leaderboard",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("api_id", sa.Uuid(), nullable=False),
        sa.Column("monthly_composite_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("rank_index", sa.Integer(), nullable=False),
        sa.Column("telemetry_samples_count", sa.Integer(), nullable=False),
        sa.Column("best_performing_region", sa.String(length=50), nullable=True),
        sa.Column("is_active_champion", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["api_id"],
            ["vnp_api_state.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_id"),
    )
    op.create_index(
        "idx_leaderboard_score_rank",
        "vnp_performance_leaderboard",
        ["monthly_composite_score", "rank_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_leaderboard_score_rank",
        table_name="vnp_performance_leaderboard",
    )
    op.drop_table("vnp_performance_leaderboard")
