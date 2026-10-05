"""add consequence redemptions (target-side authority check)"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e7a1c3b5d9f2"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consequence_redemptions",
        sa.Column("operation_id", sa.String(), primary_key=True),
        sa.Column("mount_id", sa.String(), nullable=False),
        sa.Column("receipt_id", sa.String(), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("sink_ref", sa.String(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_consequence_redemptions_mount_id", "consequence_redemptions", ["mount_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_consequence_redemptions_mount_id", table_name="consequence_redemptions")
    op.drop_table("consequence_redemptions")
