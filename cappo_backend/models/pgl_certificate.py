"""PGLCertificate — the seed provenance object.

Forward-constructed from the ``VeklomRun`` hash-field vocabulary (migration note
§1). Unlike ``VeklomRun``, governance is decided *before* execution: there is no
status-derived ``governance_decision`` default and no ``constitution_hash``
default of ``"unsealed"``. A certificate explicitly records whether it was
``persisted`` so the production fail-closed guard can reject simulated ones.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PGLCertificate(Base):
    __tablename__ = "pgl_certificates"

    certificate_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, index=True)
    workspace_id: Mapped[str] = mapped_column(String, index=True)
    actor_id: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    # Pre/post linkage (migration note §1.4).
    pre_execution_certificate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    post_execution_certificate_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Provenance hash vocabulary carried forward from VeklomRun.
    genome_hash: Mapped[str] = mapped_column(String)
    constitution_hash: Mapped[str] = mapped_column(String)
    plan_hash: Mapped[str] = mapped_column(String)
    input_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_frame_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # Pre-execution governance (computed by a governor, never status-derived).
    governance_decision: Mapped[str] = mapped_column(String)
    risk_tier: Mapped[str] = mapped_column(String)

    # Budget-as-authority inputs.
    approved_budget_cents: Mapped[int] = mapped_column(Integer, default=0)
    reserve_cents: Mapped[int] = mapped_column(Integer, default=0)

    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # Whether this certificate is backed by a durable row. Production must reject
    # non-persisted (simulated) certificates.
    persisted: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
