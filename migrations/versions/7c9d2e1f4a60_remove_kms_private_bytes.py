"""remove KMS private bytes from database custody

Revision ID: 7c9d2e1f4a60
Revises: 5e8c6076824b
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7c9d2e1f4a60"
down_revision: Union[str, None] = "5e8c6076824b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("kms_key_records") as batch_op:
        batch_op.drop_column("private_bytes")


def downgrade() -> None:
    # A downgrade restores only the nullable schema slot. Destroyed private
    # material is intentionally unrecoverable from PostgreSQL.
    with op.batch_alter_table("kms_key_records") as batch_op:
        batch_op.add_column(sa.Column("private_bytes", sa.LargeBinary(), nullable=True))
