"""ExecutionIdentity — persistent record of a minted ExecutionIdentityV1.

Columns follow the ``execution_identities`` table suggested in the EI
Implementation Plan (§Persistence model). The full canonical object (all EI
fields) is stored in ``identity_json``; the hot-path columns the gateway needs
for validation/revocation are denormalised for indexed lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionIdentity(Base):
    __tablename__ = "execution_identities"

    ei_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)

    # Backwards compatibility properties
    @property
    def execution_id(self) -> str:
        return self.ei_id

    @property
    def workspace_id(self) -> str:
        return self.tenant_id

    @property
    def directive(self) -> str:
        return self.identity_json.get("directive", "")

    @property
    def pgl_pre_certificate_id(self) -> str:
        return self.identity_json.get("pgl_pre_certificate_id") or self.pgl_certificate_id

    @property
    def genome_hash(self) -> str:
        return self.identity_json.get("genome_hash", "")

    @property
    def constitution_hash(self) -> str:
        return self.identity_json.get("constitution_hash", "")

    @property
    def plan_hash(self) -> str:
        return self.identity_json.get("plan_hash", "")

    @property
    def risk_tier(self) -> str:
        return self.identity_json.get("risk_tier", "standard")

    @property
    def scope(self) -> dict:
        return self.identity_json.get("scope", {})

    @property
    def budget_approved_cents(self) -> int:
        return self.identity_json.get("budget_approved_cents", 0)

    @property
    def budget_reserve_cents(self) -> int:
        return self.identity_json.get("budget_reserve_cents", 0)

    @property
    def delegation_depth(self) -> int:
        return self.identity_json.get("delegation_depth", 0)

    @property
    def ttl_seconds(self) -> int:
        return self.identity_json.get("ttl_seconds", 300)

    pgl_certificate_id: Mapped[str] = mapped_column(String, index=True)
    pgl_post_certificate_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # New JSON schema structure
    subject_json: Mapped[dict] = mapped_column(JSON, default=dict)
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list)
    delegation_json: Mapped[dict] = mapped_column(JSON, default=dict)
    budget_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # Hash binding
    authority_bundle_hash: Mapped[str] = mapped_column(String)
    policy_hash: Mapped[str] = mapped_column(String)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    signature: Mapped[str] = mapped_column(String)

    # Full canonical ExecutionIdentityV1 object.
    identity_json: Mapped[dict] = mapped_column(JSON, default=dict)

    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

