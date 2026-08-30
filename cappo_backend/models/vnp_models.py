"""VNP Core Models — trust and routing fabric for machine-to-machine API traffic.

Derived from the Veklom Nexus Protocol (VNP) Prototype Architecture.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cappo_backend.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VNPUser(Base):
    """1. Multi-Tenant Users and RBAC Configs"""
    __tablename__ = "vnp_users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(50),
        default="Guest Developer",
        server_default="Guest Developer"
    )
    tenant_name: Mapped[str] = mapped_column(
        String(100),
        default="Global Public Tenant",
        server_default="Global Public Tenant"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class APIState(Base):
    """2. API Registry State"""
    __tablename__ = "vnp_api_state"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vnp_providers.id", ondelete="CASCADE"), nullable=True)
    api_did: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    composite_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"))
    x402_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    stability_rating: Mapped[str] = mapped_column(String(50), default="Provisional")
    last_measured: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    provider: Mapped[VNPProvider | None] = relationship(back_populates="apis")
    telemetry: Mapped[list[RegionalTelemetry]] = relationship(back_populates="api", cascade="all, delete-orphan")
    leaderboard: Mapped[PerformanceLeaderboard | None] = relationship(back_populates="api", cascade="all, delete-orphan")
    incidents: Mapped[list[VNPIncident]] = relationship(back_populates="api", cascade="all, delete-orphan")


class VNPProvider(Base):
    """Legal and operational identity for API sellers"""
    __tablename__ = "vnp_providers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    did: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    commercial_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    apis: Mapped[list[APIState]] = relationship(back_populates="provider")


class ProbeEvent(Base):
    """Immutable signed raw measurements"""
    __tablename__ = "vnp_probe_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vnp_api_state.id", ondelete="CASCADE"), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RouteSnapshot(Base):
    """Periodic derived route recommendations by region and policy"""
    __tablename__ = "vnp_route_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_name: Mapped[str] = mapped_column(String(100), default="default")
    recommendations_json: Mapped[dict] = mapped_column(JSON, default=dict) # List of API DIDs with weights
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VNPSDKCredential(Base):
    """Auth material, rate plans, and policy entitlements for SDK use"""
    __tablename__ = "vnp_sdk_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vnp_users.id", ondelete="CASCADE"), nullable=False)
    api_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    policy_entitlements: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VNPValidator(Base):
    """Validator identities, stake state, and status"""
    __tablename__ = "vnp_validators"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    did: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    stake_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(50), default="Active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VNPIncident(Base):
    """Outages, degraded regions, fraud signals, or dispute cases"""
    __tablename__ = "vnp_incidents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vnp_api_state.id", ondelete="CASCADE"), nullable=False)
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(100), nullable=False) # e.g., "Outage", "Latency Spike"
    status: Mapped[str] = mapped_column(String(50), default="Open")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    api: Mapped[APIState] = relationship(back_populates="incidents")


class RegionalTelemetry(Base):
    """3. Regional Benchmark Aggregations (Continuous Telemetry Ingestion)"""
    __tablename__ = "vnp_regional_telemetry"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vnp_api_state.id", ondelete="CASCADE"), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    p50_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    p95_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    p99_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_rate_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"))
    uptime_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("100.00"))
    throughput_rps: Mapped[int] = mapped_column(Integer, default=0)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    api: Mapped[APIState] = relationship(back_populates="telemetry")


class VNPTransaction(Base):
    """4. High-Stakes Transactions (Escrow Payments via x402 / MPP)"""
    __tablename__ = "vnp_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    buyer_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vnp_users.id", ondelete="SET NULL"), nullable=True)
    target_api_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vnp_api_state.id", ondelete="RESTRICT"), nullable=False)
    microtransaction_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(15, 6), nullable=False)
    gas_fee_usd: Mapped[Decimal] = mapped_column(Numeric(15, 6), default=Decimal("0.000000"))
    payment_status: Mapped[str] = mapped_column(String(50), default="Pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PerformanceLeaderboard(Base):
    """5. Real-Time Leaderboard Tracking"""
    __tablename__ = "vnp_performance_leaderboard"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vnp_api_state.id", ondelete="CASCADE"), unique=True, nullable=False)
    monthly_composite_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"))
    rank_index: Mapped[int] = mapped_column(Integer, nullable=False)
    telemetry_samples_count: Mapped[int] = mapped_column(Integer, default=0)
    best_performing_region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active_champion: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    api: Mapped[APIState] = relationship(back_populates="leaderboard")


class ComplianceAuditLog(Base):
    """6. Fully Encrypted Audit Logs (Supports Compliance Audits)"""
    __tablename__ = "vnp_compliance_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vnp_users.id", ondelete="SET NULL"), nullable=True)
    tenant_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action_type: Mapped[str] = mapped_column(String(255), nullable=False)
    affected_entity: Mapped[str] = mapped_column(String(255), nullable=False)
    hash_payload: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# Indices for performance
Index("idx_api_state_composite", APIState.composite_score.desc())
Index("idx_telemetry_measured", RegionalTelemetry.measured_at.desc())
Index("idx_transactions_micro", VNPTransaction.microtransaction_id)
Index("idx_leaderboard_score_rank", PerformanceLeaderboard.monthly_composite_score.desc(), PerformanceLeaderboard.rank_index.asc())
