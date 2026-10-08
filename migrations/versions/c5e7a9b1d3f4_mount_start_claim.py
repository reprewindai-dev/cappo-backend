"""Mount start claim: the one operation granted the right to start under a mount.

Revision ID: c5e7a9b1d3f4
Revises: b4d2e6f8a1c3
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f4"
down_revision: Union[str, None] = "b4d2e6f8a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable and additive: existing mounts have no claim and existing packages do not require one.
    op.add_column("capability_mounts", sa.Column("start_claim_operation_id", sa.String(), nullable=True))
    op.add_column("capability_mounts", sa.Column("start_claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("capability_mounts", "start_claimed_at")
    op.drop_column("capability_mounts", "start_claim_operation_id")
