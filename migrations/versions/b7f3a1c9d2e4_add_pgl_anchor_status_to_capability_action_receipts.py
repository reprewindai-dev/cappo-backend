"""add pgl anchor status to capability action receipts

Revision ID: b7f3a1c9d2e4
Revises: a83026ac71d1
"""

from alembic import op
import sqlalchemy as sa


revision = "b7f3a1c9d2e4"
down_revision = "a83026ac71d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("capability_action_receipts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pgl_anchor_status", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("capability_action_receipts", schema=None) as batch_op:
        batch_op.drop_column("pgl_anchor_status")
