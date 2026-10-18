"""add_authority_epoch_to_capability_mounts_and_consequence_events

Revision ID: fa640053636b
Revises: c5e7a9b1d3f4
Create Date: 2026-10-17 02:49:36.888634

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fa640053636b'
down_revision: Union[str, None] = 'c5e7a9b1d3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    mount_cols = [c["name"] for c in inspector.get_columns("capability_mounts")]
    if "authority_epoch" not in mount_cols:
        op.add_column("capability_mounts", sa.Column("authority_epoch", sa.Integer(), nullable=False, server_default="1"))
        op.create_index("ix_capability_mounts_authority_epoch", "capability_mounts", ["authority_epoch"])

    event_cols = [c["name"] for c in inspector.get_columns("consequence_execution_events")]
    if "authority_epoch" not in event_cols:
        op.add_column("consequence_execution_events", sa.Column("authority_epoch", sa.Integer(), nullable=True, server_default="1"))
        op.create_index("ix_consequence_execution_events_authority_epoch", "consequence_execution_events", ["authority_epoch"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    event_cols = [c["name"] for c in inspector.get_columns("consequence_execution_events")]
    if "authority_epoch" in event_cols:
        op.drop_index("ix_consequence_execution_events_authority_epoch", table_name="consequence_execution_events")
        op.drop_column("consequence_execution_events", "authority_epoch")

    mount_cols = [c["name"] for c in inspector.get_columns("capability_mounts")]
    if "authority_epoch" in mount_cols:
        op.drop_index("ix_capability_mounts_authority_epoch", table_name="capability_mounts")
        op.drop_column("capability_mounts", "authority_epoch")
