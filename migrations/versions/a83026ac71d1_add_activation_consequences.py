"""add durable Veklom Activation consequence target

Revision ID: a83026ac71d1
Revises: 7c9d2e1f4a60
"""

import sqlalchemy as sa

from alembic import op

revision = "a83026ac71d1"
down_revision = "7c9d2e1f4a60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activation_consequences",
        sa.Column("consequence_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("operation_id", sa.String(), nullable=False),
        sa.Column("mount_id", sa.String(), nullable=False),
        sa.Column("receipt_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("marker_value", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("consequence_id"),
        sa.UniqueConstraint(
            "execution_id",
            name="uq_activation_consequence_execution_id",
        ),
        sa.UniqueConstraint(
            "operation_id",
            name="uq_activation_consequence_operation_id",
        ),
    )
    op.create_index(
        op.f("ix_activation_consequences_workspace_id"),
        "activation_consequences",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activation_consequences_execution_id"),
        "activation_consequences",
        ["execution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activation_consequences_operation_id"),
        "activation_consequences",
        ["operation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activation_consequences_mount_id"),
        "activation_consequences",
        ["mount_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activation_consequences_receipt_id"),
        "activation_consequences",
        ["receipt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_activation_consequences_receipt_id"),
        table_name="activation_consequences",
    )
    op.drop_index(
        op.f("ix_activation_consequences_mount_id"),
        table_name="activation_consequences",
    )
    op.drop_index(
        op.f("ix_activation_consequences_operation_id"),
        table_name="activation_consequences",
    )
    op.drop_index(
        op.f("ix_activation_consequences_execution_id"),
        table_name="activation_consequences",
    )
    op.drop_index(
        op.f("ix_activation_consequences_workspace_id"),
        table_name="activation_consequences",
    )
    op.drop_table("activation_consequences")
