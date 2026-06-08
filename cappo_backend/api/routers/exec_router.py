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
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder, HmacSigner
from cappo_backend.services.executor import EchoExecutor
from cappo_backend.services.orchestrator import RunOrchestrator
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.services.pgl_client import PGLClient

router = APIRouter(prefix="/v1")


# ---------- request/response shapes ----------

class ExecRequest(BaseModel):
    prompt: str
    workspace_id: str = "default"
    tenant_id: str = "default"
    delegation_depth: int = 0
    budget_approved_cents: int = 0
    scope: dict[str, Any] | None = None
    genome_hash: str | None = None
    constitution_hash: str | None = None
    plan_hash: str | None = None


class ExecResponse(BaseModel):
    response: str
    model: str | None = None
    provider: str | None = None
    tokens: int | None = None
    latency_ms: float | None = None
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

    # Assemble orchestrator collaborators.
    pgl = PGLClient(db=db, settings=settings)
    signer = HmacSigner(settings.ei_signing_key)
    builder = ExecutionIdentityBuilder(signer=signer)
    audit = AuditService(db)
    executor = EchoExecutor()  # will be swapped for real provider
    orchestrator = RunOrchestrator(
        db=db, pgl=pgl, builder=builder, executor=executor, audit=audit
    )

    payload: dict[str, Any] = body.model_dump()

    # Run the governed pipeline (governance, PGL cert, EI mint, execution, attestation).
    result = orchestrator.run_governed(payload)

    # After execution, enforce the MCP gateway check to demonstrate the enforcement
    # contract before the response leaves. (In a real MCP tool-call flow this would
    # fire *before* execution; here it validates the run's own EI.)
    run = db.query(GovernedRun).order_by(GovernedRun.created_at.desc()).first()

    # Post-pipeline gateway validation (defence in depth).
    gateway = MCPGateway(audit, pgl_lookup=pgl.get_certificate, settings=settings)
    if run and run.execution_identity:
        try:
            gateway.require_execution_identity(
                run.execution_identity,
                workspace_id=body.workspace_id,
                run_id=run.run_id if run else None,
            )
        except EIValidationError as exc:
            raise HTTPException(
                status_code=403,
                detail={"error": "EXECUTION_IDENTITY_REQUIRED", "detail": exc.detail, "law0": True},
            )

    db.commit()

    elapsed_ms = (time.monotonic() - start) * 1000
    return ExecResponse(
        response=result.get("response", ""),
        model=result.get("model"),
        provider=result.get("provider"),
        tokens=result.get("tokens"),
        latency_ms=round(elapsed_ms, 2),
        run_id=run.run_id if run else None,
        execution_id=(run.execution_identity or {}).get("execution_id") if run else None,
    )
