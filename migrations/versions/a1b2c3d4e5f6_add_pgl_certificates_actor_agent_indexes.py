"""add actor_id/agent_id columns and indexes on pgl_certificates

Revision ID: a1b2c3d4e5f6
Revises: 7386bf0ab803
Create Date: 2026-06-15 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '7386bf0ab803'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in the given table."""
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in cols


def _index_exists(index_name: str) -> bool:
    """Check if an index already exists."""
    bind = op.get_bind()
    insp = inspect(bind)
    # get_indexes returns list of dicts; check all tables that matter
    for table in ["pgl_certificates"]:
        try:
            idxs = [i["name"] for i in insp.get_indexes(table)]
            if index_name in idxs:
                return True
        except Exception:
            pass
    return False


def upgrade() -> None:
    # Ensure actor_id column exists before indexing
    if not _column_exists("pgl_certificates", "actor_id"):
        op.add_column(
            "pgl_certificates",
            sa.Column("actor_id", sa.String(), nullable=True)
        )

    # Ensure agent_id column exists before indexing
    if not _column_exists("pgl_certificates", "agent_id"):
        op.add_column(
            "pgl_certificates",
            sa.Column("agent_id", sa.String(), nullable=True)
        )

    # Create indexes (guarded so re-runs don't fail)
    with op.batch_alter_table('pgl_certificates', schema=None) as batch_op:
        if not _index_exists('ix_pgl_certificates_actor_id'):
            batch_op.create_index(
                batch_op.f('ix_pgl_certificates_actor_id'),
                ['actor_id'],
                unique=False
            )
        if not _index_exists('ix_pgl_certificates_agent_id'):
            batch_op.create_index(
                batch_op.f('ix_pgl_certificates_agent_id'),
                ['agent_id'],
                unique=False
            )


def downgrade() -> None:
    with op.batch_alter_table('pgl_certificates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pgl_certificates_agent_id'))
        batch_op.drop_index(batch_op.f('ix_pgl_certificates_actor_id'))
