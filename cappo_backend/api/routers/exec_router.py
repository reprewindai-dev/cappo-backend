"""Governed /v1/exec route (Option A — routes through orchestrator).

Migration note §5 / EI Plan §/v1/exec migration: replaces the old ungoverned
``POST /v1/exec`` with a single governed entry path that inherits governance,
PGL, EI minting, execution, and attestation from the orchestrator. Preserves
the ``ExecResponse`` contract (response/model/provider/tokens/latency/log_id/conversation_id).

There is deliberately no alternate execution path; the old public-allowlist
bypass is gone.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import get_session
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder
from cappo_backend.services.enterprise_signer import create_enterprise_signer_from_settings
from cappo_backend.services.orchestrator import RunOrchestrator
from cappo_backend.services.payment_gate import PaymentGate, PaymentRequiredError
from cappo_backend.services.pgl_adapter import create_pgl_client
from cappo_backend.services.providers import build_executor
from cappo_backend.services.revocation_service import RevocationService

router = APIRouter(prefix="/v1")


# ---------- request/response shapes ----------


class ExecRequest(BaseModel):
    prompt: str
    agent_id: str | None = None  # Veklom agent ID (e.g., "agent_alpha")
    pgl_id: str | None = None  # User's PGL identity
    workspace_id: str = "default"
    tenant_id: str = "default"
    delegation_depth: int = 0
    budget_approved_cents: int = 0
    action_cost_cents: int = 0
    scope: dict[str, Any] | None = None
    genome_hash: str | None = None
    constitution_hash: str | None = None
    plan_hash: str | None = None
    action: str | None = None
    directive: str | None = None
    risk_tier: str | None = None


class ExecResponse(BaseModel):
    response: str
    model: str | None = None
    provider: str | None = None
    tokens: int | None = None
    latency_ms: float | None = None
    cached: bool | None = None
    cache_tier: str | None = None
    log_id: str | None = None
    run_id: str | None = None
    execution_id: str | None = None


# ---------- route ----------


@router.post("/exec", response_model=ExecResponse)
def governed_exec(
    body: ExecRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ExecResponse:
    """Single governed execution entry path (Option A)."""
    start = time.monotonic()

    audit = AuditService(db)

    # Payment/kill-switch gate runs FIRST: a 402 takes precedence over the LAW 0
    # 403 (migration note §7 / EI Plan §Priority rule). It short-circuits before
    # any governed pipeline work or EI enforcement.
    try:
        PaymentGate(db).check(body.workspace_id, cost_cents=body.action_cost_cents)
    except PaymentRequiredError as exc:
        raise HTTPException(
            status_code=402,
            detail={"error": "PAYMENT_REQUIRED", "detail": exc.detail, "reason": exc.reason},
        )

    # Assemble orchestrator collaborators.
    # Use real veklom-byos-backend if configured, otherwise local DB
    pgl = create_pgl_client(db=db, settings=settings, use_veklom=True)
    signer = create_enterprise_signer_from_settings(settings)
    builder = ExecutionIdentityBuilder(signer=signer)
    executor = build_executor(settings)  # echo stub or real provider(s) per config
    revocation = RevocationService(db, audit)
    # The gateway enforces LAW 0 *inside* the pipeline, before the side effect.
    gateway = MCPGateway(
        audit,
        pgl_lookup=pgl.get_certificate,
        revocation_lookup=revocation.is_revoked,
        settings=settings,
    )
    from cappo_backend.adapters.local import SQLiteGraphAdapter, SQLiteStoreAdapter
    from cappo_backend.api.routers.genome_router import _global_cache, _global_queue
    from cappo_backend.services.genome_service import GenomeService

    genome_service = GenomeService(
        store=SQLiteStoreAdapter(db),
        graph=SQLiteGraphAdapter(db),
        cache=_global_cache,
        queue=_global_queue,
    )
    orchestrator = RunOrchestrator(
        db=db,
        pgl=pgl,
        builder=builder,
        executor=executor,
        audit=audit,
        gateway=gateway,
        genome_service=genome_service,
    )

    payload: dict[str, Any] = body.model_dump()

    # Run the governed pipeline (governance, PGL cert, EI mint, LAW 0 enforcement,
    # execution, post-cert + attestation). A failed EI check raises before any
    # side effect and is mapped to the 403 LAW 0 contract.
    try:
        result = orchestrator.run_governed(payload)
    except EIValidationError as exc:
        db.commit()  # persist the FAILED run + law0_violation audit event
        raise HTTPException(
            status_code=403,
            detail={
                "error": "EXECUTION_IDENTITY_REQUIRED",
                "code": getattr(exc, "code", "LAW0_EI_INVALID"),
                "detail": exc.detail,
                "law0": True,
                "incident_logged": True,
            },
        )

    run = orchestrator.last_run
    db.commit()

    elapsed_ms = (time.monotonic() - start) * 1000
    return ExecResponse(
        response=result.get("response", ""),
        model=result.get("model"),
        provider=result.get("provider"),
        tokens=result.get("tokens"),
        latency_ms=round(elapsed_ms, 2),
        cached=result.get("cached"),
        cache_tier=result.get("cache_tier"),
        run_id=run.run_id if run else None,
        execution_id=(run.execution_identity or {}).get("execution_id") if run else None,
    )
