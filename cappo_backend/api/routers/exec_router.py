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
from cappo_backend.services.orchestrator import (
    GovernanceDeniedError,
    MissingGovernanceDecisionError,
    RunOrchestrator,
)
from cappo_backend.services.payment_gate import PaymentGate, PaymentRequiredError
from cappo_backend.services.pgl_adapter import create_pgl_client
from cappo_backend.services.providers import build_executor
from cappo_backend.services.revocation_service import RevocationService

router = APIRouter(prefix="/v1")


# ---------- request/response shapes ----------

class ExecRequest(BaseModel):
    prompt: str
    agent_id: str | None = None  # Veklom agent ID (e.g., "agent_alpha")
    pgl_id: str | None = None    # User's PGL identity
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
    security: dict[str, Any] | None = None
    execution_mode: str = "live"



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
    links: dict[str, Any] | None = None


# ---------- route ----------

def _check_payment(db: Session, workspace_id: str, cost_cents: int) -> None:
    try:
        PaymentGate(db).check(workspace_id, cost_cents=cost_cents)
    except PaymentRequiredError as exc:
        raise HTTPException(
            status_code=402,
            detail={"error": "PAYMENT_REQUIRED", "detail": exc.detail, "reason": exc.reason},
        )

def _build_orchestrator(db: Session, settings: Settings, audit: AuditService) -> RunOrchestrator:
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
    return RunOrchestrator(
        db=db,
        pgl=pgl,
        builder=builder,
        executor=executor,
        audit=audit,
        gateway=gateway,
        genome_service=genome_service,
    )

def _execute_run(orchestrator: RunOrchestrator, payload: dict[str, Any], db: Session) -> dict[str, Any]:
    try:
        return orchestrator.run_governed(payload)
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
    except MissingGovernanceDecisionError as exc:
        db.commit()  # persist the FAILED run + audit event
        raise HTTPException(
            status_code=400,
            detail={
                "error": "CAPPO_GOVERNANCE_DECISION_REQUIRED",
                "detail": str(exc),
                "fail_closed": True,
            },
        )
    except GovernanceDeniedError as exc:
        db.commit()  # persist the FAILED run + audit event
        raise HTTPException(
            status_code=403,
            detail={
                "error": "CAPPO_GOVERNANCE_DENIED",
                "detail": str(exc),
                "fail_closed": True,
            },
        )

def _resolve_capi_gatekeeper_public_key(settings: Settings, body: ExecRequest) -> str:
    """Return the configured cAPI verification key or fail closed when needed."""
    public_key = settings.capi_gatekeeper_public_key.strip()
    has_security = body.security is not None

    if not has_security:
        if not settings.capi_external_validation_enabled and not settings.is_production:
            return ""
        raise HTTPException(
            status_code=401,
            detail={
                "error": "CAPI_SIGNED_SECURITY_REQUIRED",
                "detail": "/v1/exec requests must include a signed security envelope.",
            },
        )

    if not public_key:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "CAPI_GATEKEEPER_KEY_UNAVAILABLE",
                "detail": "cAPI Gatekeeper requires CAPI_GATEKEEPER_PUBLIC_KEY to be configured.",
            },
        )

    return public_key

@router.post("/exec", response_model=ExecResponse)
async def governed_exec(
    body: ExecRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ExecResponse:
    """Single governed execution entry path (Option A)."""
    start = time.monotonic()
    audit = AuditService(db)

    # cAPI PHASE 1: Gatekeeper Enforcement
    from cappo_backend.core.capi_pipeline import enforce_capi_pipeline, seal_evidence_pack
    test_only_echo = settings.environment.lower() == "test" and settings.executor_mode == "echo"
    capi_public_key = "" if test_only_echo else _resolve_capi_gatekeeper_public_key(settings, body)
    
    # We construct the payload expected by cAPI
    capi_payload = {
        "action": body.action or "execute",
        "data": body.model_dump(),
        "security": body.security
    }
    
    # Run the strict cAPI pipeline (Phases 1-6)
    if test_only_echo:
        capi_result = {"evidence_id": "test-only"}
    else:
        try:
            capi_result = await enforce_capi_pipeline(body.pgl_id or "unknown", capi_payload, capi_public_key)
        except Exception as e:
            # If security fails, we don't even reach orchestration
            raise HTTPException(status_code=401, detail=f"cAPI Gatekeeper Reject: {str(e)}")

    _check_payment(db, body.workspace_id, body.action_cost_cents)
    orchestrator = _build_orchestrator(db, settings, audit)
    result = _execute_run(orchestrator, body.model_dump(), db)

    run = orchestrator.last_run
    db.commit()
    
    # cAPI PHASE 7-9: Evidence Sealing
    if not test_only_echo:
        await seal_evidence_pack(capi_result["evidence_id"], result)

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
        links={
            "audit": {"href": f"/api/v1/gpc/audit/{run.run_id if run else 'unknown'}", "method": "GET"},
            "stake": {"href": "/api/v1/vnp/stake", "method": "POST"},
            "evidence": {"href": "/api/v1/evidence/verify", "method": "POST"}
        }
    )
