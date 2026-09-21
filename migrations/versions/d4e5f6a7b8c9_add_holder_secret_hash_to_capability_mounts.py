"""add one-mount holder credential hash"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c9d4e2f7a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("capability_mounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("holder_secret_hash", sa.String(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("capability_mounts", schema=None) as batch_op:
        batch_op.drop_column("holder_secret_hash")
