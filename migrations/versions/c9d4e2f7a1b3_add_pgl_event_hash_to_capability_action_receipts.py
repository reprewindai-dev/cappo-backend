"""add pgl event hash to capability action receipts

Revision ID: c9d4e2f7a1b3
Revises: b7f3a1c9d2e4
"""

import sqlalchemy as sa

from alembic import op

revision = "c9d4e2f7a1b3"
down_revision = "b7f3a1c9d2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("capability_action_receipts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pgl_event_hash", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("capability_action_receipts", schema=None) as batch_op:
        batch_op.drop_column("pgl_event_hash")
