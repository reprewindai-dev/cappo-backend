"""bring the VNP schema under migration control

Revision ID: b7d1e4f6a902
Revises: 9978153219c2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d1e4f6a902"
down_revision: Union[str, None] = "9978153219c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEXES = {
    "vnp_api_state": ("idx_api_state_composite",),
    "vnp_regional_telemetry": ("idx_telemetry_measured",),
    "vnp_transactions": ("idx_transactions_micro",),
    "vnp_performance_leaderboard": ("idx_leaderboard_score_rank",),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    created_tables: set[str] = set()

    if not inspector.has_table("vnp_users"):
        op.create_table(
            "vnp_users",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column(
                "role",
                sa.String(length=50),
                nullable=False,
                server_default="Guest Developer",
            ),
            sa.Column(
                "tenant_name",
                sa.String(length=100),
                nullable=False,
                server_default="Global Public Tenant",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email"),
        )
        created_tables.add("vnp_users")

    if not inspector.has_table("vnp_providers"):
        op.create_table(
            "vnp_providers",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("did", sa.String(length=100), nullable=False),
            sa.Column("commercial_profile", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("did"),
        )
        created_tables.add("vnp_providers")

    if not inspector.has_table("vnp_api_state"):
        op.create_table(
            "vnp_api_state",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("provider_id", sa.Uuid(), nullable=True),
            sa.Column("api_did", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("endpoint", sa.Text(), nullable=False),
            sa.Column("version", sa.String(length=50), nullable=False),
            sa.Column("composite_score", sa.Numeric(precision=5, scale=2), nullable=False),
            sa.Column("x402_compliant", sa.Boolean(), nullable=False),
            sa.Column(
                "stability_rating",
                sa.String(length=50),
                nullable=False,
            ),
            sa.Column("last_measured", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["provider_id"],
                ["vnp_providers.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("api_did"),
        )
        created_tables.add("vnp_api_state")

    if not inspector.has_table("vnp_probe_events"):
        op.create_table(
            "vnp_probe_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("api_id", sa.Uuid(), nullable=False),
            sa.Column("worker_id", sa.String(length=100), nullable=False),
            sa.Column("region", sa.String(length=50), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("signature", sa.String(length=255), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["api_id"],
                ["vnp_api_state.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        created_tables.add("vnp_probe_events")

    if not inspector.has_table("vnp_route_snapshots"):
        op.create_table(
            "vnp_route_snapshots",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("region", sa.String(length=50), nullable=False),
            sa.Column(
                "policy_name",
                sa.String(length=100),
                nullable=False,
            ),
            sa.Column("recommendations_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        created_tables.add("vnp_route_snapshots")

    if not inspector.has_table("vnp_sdk_credentials"):
        op.create_table(
            "vnp_sdk_credentials",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("customer_id", sa.Uuid(), nullable=False),
            sa.Column("api_key", sa.String(length=100), nullable=False),
            sa.Column("policy_entitlements", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["vnp_users.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("api_key"),
        )
        created_tables.add("vnp_sdk_credentials")

    if not inspector.has_table("vnp_validators"):
        op.create_table(
            "vnp_validators",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("did", sa.String(length=100), nullable=False),
            sa.Column("stake_amount", sa.Numeric(precision=20, scale=6), nullable=False),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("did"),
        )
        created_tables.add("vnp_validators")

    if not inspector.has_table("vnp_incidents"):
        op.create_table(
            "vnp_incidents",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("api_id", sa.Uuid(), nullable=False),
            sa.Column("region", sa.String(length=50), nullable=True),
            sa.Column("incident_type", sa.String(length=100), nullable=False),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
            ),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["api_id"],
                ["vnp_api_state.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        created_tables.add("vnp_incidents")

    if not inspector.has_table("vnp_regional_telemetry"):
        op.create_table(
            "vnp_regional_telemetry",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("api_id", sa.Uuid(), nullable=False),
            sa.Column("region", sa.String(length=50), nullable=False),
            sa.Column("p50_latency_ms", sa.Integer(), nullable=False),
            sa.Column("p95_latency_ms", sa.Integer(), nullable=False),
            sa.Column("p99_latency_ms", sa.Integer(), nullable=False),
            sa.Column(
                "error_rate_percent",
                sa.Numeric(precision=5, scale=2),
                nullable=False,
            ),
            sa.Column(
                "uptime_percent",
                sa.Numeric(precision=5, scale=2),
                nullable=False,
            ),
            sa.Column("throughput_rps", sa.Integer(), nullable=False),
            sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["api_id"],
                ["vnp_api_state.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        created_tables.add("vnp_regional_telemetry")

    if not inspector.has_table("vnp_transactions"):
        op.create_table(
            "vnp_transactions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("buyer_user_id", sa.Uuid(), nullable=True),
            sa.Column("target_api_id", sa.Uuid(), nullable=False),
            sa.Column("microtransaction_id", sa.String(length=255), nullable=False),
            sa.Column("amount_usd", sa.Numeric(precision=15, scale=6), nullable=False),
            sa.Column(
                "gas_fee_usd",
                sa.Numeric(precision=15, scale=6),
                nullable=False,
            ),
            sa.Column(
                "payment_status",
                sa.String(length=50),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["buyer_user_id"],
                ["vnp_users.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["target_api_id"],
                ["vnp_api_state.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("microtransaction_id"),
        )
        created_tables.add("vnp_transactions")

    if not inspector.has_table("vnp_performance_leaderboard"):
        op.create_table(
            "vnp_performance_leaderboard",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("api_id", sa.Uuid(), nullable=False),
            sa.Column(
                "monthly_composite_score",
                sa.Numeric(precision=5, scale=2),
                nullable=False,
            ),
            sa.Column("rank_index", sa.Integer(), nullable=False),
            sa.Column("telemetry_samples_count", sa.Integer(), nullable=False),
            sa.Column("best_performing_region", sa.String(length=50), nullable=True),
            sa.Column("is_active_champion", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["api_id"],
                ["vnp_api_state.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("api_id"),
        )
        created_tables.add("vnp_performance_leaderboard")

    if not inspector.has_table("vnp_compliance_audit_log"):
        op.create_table(
            "vnp_compliance_audit_log",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("actor_id", sa.Uuid(), nullable=True),
            sa.Column("tenant_name", sa.String(length=100), nullable=False),
            sa.Column("action_type", sa.String(length=255), nullable=False),
            sa.Column("affected_entity", sa.String(length=255), nullable=False),
            sa.Column("hash_payload", sa.String(length=64), nullable=False),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["actor_id"],
                ["vnp_users.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        created_tables.add("vnp_compliance_audit_log")

    if "vnp_api_state" in created_tables:
        op.create_index(
            "idx_api_state_composite",
            "vnp_api_state",
            [sa.text("composite_score DESC")],
            unique=False,
        )
    if "vnp_regional_telemetry" in created_tables:
        op.create_index(
            "idx_telemetry_measured",
            "vnp_regional_telemetry",
            [sa.text("measured_at DESC")],
            unique=False,
        )
    if "vnp_transactions" in created_tables:
        op.create_index(
            "idx_transactions_micro",
            "vnp_transactions",
            ["microtransaction_id"],
            unique=False,
        )
    if "vnp_performance_leaderboard" in created_tables:
        op.create_index(
            "idx_leaderboard_score_rank",
            "vnp_performance_leaderboard",
            [sa.text("monthly_composite_score DESC"), sa.text("rank_index ASC")],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, index_names in _INDEXES.items():
        if inspector.has_table(table_name):
            existing_indexes = {
                index["name"] for index in inspector.get_indexes(table_name)
            }
            for index_name in index_names:
                if index_name in existing_indexes:
                    op.drop_index(index_name, table_name=table_name)

    for table_name in (
        "vnp_compliance_audit_log",
        "vnp_performance_leaderboard",
        "vnp_transactions",
        "vnp_regional_telemetry",
        "vnp_incidents",
        "vnp_validators",
        "vnp_sdk_credentials",
        "vnp_route_snapshots",
        "vnp_probe_events",
        "vnp_api_state",
        "vnp_providers",
        "vnp_users",
    ):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
