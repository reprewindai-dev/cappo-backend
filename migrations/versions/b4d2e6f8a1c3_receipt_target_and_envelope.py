"""receipt names the target and the bound operation envelope

A receipt's content_hash must be recomputable from the stored row. The signed
canonical receipt now names the target dispatched to and, for an operation-bound
mount, the envelope digest of the one operation it allowed, so both are stored.

Revision ID: b4d2e6f8a1c3
Revises: e7a1c3b5d9f2
Create Date: 2026-10-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4d2e6f8a1c3'
down_revision: Union[str, None] = 'e7a1c3b5d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('capability_action_receipts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('target_ref', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('envelope_digest', sa.String(), nullable=True))
        # NULL for every existing row = v1: historical receipts keep their original
        # canonical form and still verify exactly as written. Nothing is backfilled.
        batch_op.add_column(sa.Column('receipt_schema_version', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('capability_action_receipts', schema=None) as batch_op:
        batch_op.drop_column('receipt_schema_version')
        batch_op.drop_column('envelope_digest')
        batch_op.drop_column('target_ref')
