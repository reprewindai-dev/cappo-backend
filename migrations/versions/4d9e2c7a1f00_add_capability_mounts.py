"""add durable capability mounts"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4d9e2c7a1f00"
down_revision: Union[str, None] = "9c2f42d7a7d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "capability_mounts",
        sa.Column("mount_id", sa.String(), nullable=False),
        sa.Column("token_id", sa.String(), nullable=False),
        sa.Column("token_nonce", sa.String(), nullable=False),
        sa.Column("mount_json", sa.JSON(), nullable=False),
        sa.Column("token_json", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nonce_consumed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("anchor_status", sa.String(), nullable=False, server_default="not_applicable"),
        sa.Column("anchor_id", sa.String(), nullable=True),
        sa.Column("anchor_detail", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("mount_id"),
    )
    with op.batch_alter_table("capability_mounts") as batch_op:
        batch_op.create_index("ix_capability_mounts_token_id", ["token_id"], unique=True)
        batch_op.create_index("ix_capability_mounts_token_nonce", ["token_nonce"], unique=True)
        batch_op.create_index("ix_capability_mounts_expires_at", ["expires_at"], unique=False)
        batch_op.create_index("ix_capability_mounts_terminated", ["terminated"], unique=False)


def downgrade() -> None:
    op.drop_table("capability_mounts")
