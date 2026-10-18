"""add durable VNP Interlink replay nonces"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9d2a7e5c4b11"
down_revision: Union[str, None] = "8c1f2b4a9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vnp_interlink_nonces",
        sa.Column("nonce", sa.String(length=255), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("nonce"),
    )
    with op.batch_alter_table("vnp_interlink_nonces") as batch_op:
        batch_op.create_index(
            "ix_vnp_interlink_nonces_expires_at",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("vnp_interlink_nonces")
