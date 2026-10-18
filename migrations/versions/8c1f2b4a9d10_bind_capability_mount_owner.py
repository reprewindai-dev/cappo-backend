"""bind capability mounts to authenticated principal and workspace"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8c1f2b4a9d10"
down_revision: Union[str, None] = "4d9e2c7a1f00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("capability_mounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "owner_principal",
                sa.String(),
                nullable=False,
                server_default="legacy-unbound",
            )
        )
        batch_op.add_column(
            sa.Column(
                "owner_workspace",
                sa.String(),
                nullable=False,
                server_default="legacy-unbound",
            )
        )
        batch_op.create_index(
            "ix_capability_mounts_owner_principal",
            ["owner_principal"],
            unique=False,
        )
        batch_op.create_index(
            "ix_capability_mounts_owner_workspace",
            ["owner_workspace"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("capability_mounts") as batch_op:
        batch_op.drop_index("ix_capability_mounts_owner_workspace")
        batch_op.drop_index("ix_capability_mounts_owner_principal")
        batch_op.drop_column("owner_workspace")
        batch_op.drop_column("owner_principal")
