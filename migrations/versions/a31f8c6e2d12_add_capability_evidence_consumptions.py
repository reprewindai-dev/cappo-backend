"""add durable capability evidence replay store"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a31f8c6e2d12"
down_revision: Union[str, None] = "9d2a7e5c4b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "capability_evidence_consumptions",
        sa.Column("jti", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("mount_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti"),
    )
    with op.batch_alter_table("capability_evidence_consumptions") as batch_op:
        batch_op.create_index(
            "ix_capability_evidence_consumptions_kind",
            ["kind"],
            unique=False,
        )
        batch_op.create_index(
            "ix_capability_evidence_consumptions_mount_id",
            ["mount_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("capability_evidence_consumptions")
