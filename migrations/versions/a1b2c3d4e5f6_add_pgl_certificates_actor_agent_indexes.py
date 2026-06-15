"""add indexes on pgl_certificates.actor_id and agent_id

Revision ID: a1b2c3d4e5f6
Revises: 7386bf0ab803
Create Date: 2026-06-15 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '7386bf0ab803'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('pgl_certificates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pgl_certificates_actor_id'), ['actor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_pgl_certificates_agent_id'), ['agent_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('pgl_certificates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pgl_certificates_agent_id'))
        batch_op.drop_index(batch_op.f('ix_pgl_certificates_actor_id'))
