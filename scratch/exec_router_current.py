"""Governed /v1/exec route (Option A — routes through orchestrator).

Migration note §5 / EI Plan §/v1/exec migration: replaces the old ungoverned
``POST /v1/exec`` with a single governed entry path that inherits governance,
PGL, EI minting, execution, and attestation from the orchestrator. Preserves
the ``ExecResponse`` contract (response/model/provider/tokens/latency/log_id/conversation_id).

There is deliberately no alternate execution path; the old public-allowlist
bypass is gone.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cappo_backend.authorization.cappo_auth import CappoPreauthorizationEnforcer
from cappo_backend.authorization.errors import CappoAuthorizationError
from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import get_session
from cappo_backend.identity.errors import IdentityValidationError
from cappo_backend.identity.middleware import RouteClassification, WIDMiddlewareContext
from cappo_backend.identity.models import (
    AuthorityArtifact,
    ExecutionContextToken,
    WorkloadIdentityToken,
    WorkloadProofToken,
)
from cappo_backend.identity.replay_cache import RedisReplayCache, ReplayCache
from cappo_backend.identity.validator import IdentityValidator
from cappo_backend.security.http_signatures import (
    SignatureVerificationError,
    verify_rfc9421_request,
)
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.eee import EEEBuilder, build_terminal_eee
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder
from cappo_backend.services.enterprise_signer import create_enterprise_signer_from_settings
from cappo_backend.services.executor import (
    ExecutorUnavailableError,
    ProviderCredentialRejectedError,
    ProviderPolicyRejectedError,
    ProviderRateLimitedError,
    TerminalExecutionError,
)
from cappo_backend.services.orchestrator import (
    GovernanceDeniedError,
    MissingGovernanceDecisionError,
    RunOrchestrator,
    RuntimeOwnershipError,
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

def _check_payment(db: Session, workspace_id: str, cost_cents: int, settings: Settings, app: Any = None) -> PaymentGate:
    redis_client = getattr(app.state, "redis_client", None) if app and hasattr(app, "state") else None
    gate = PaymentGate(db, redis_client=redis_client, settings=settings)
    try:
        gate.check(workspace_id, cost_cents=cost_cents)
    except PaymentRequiredError as exc:
        raise HTTPException(
            status_code=402,
            detail={"error": "PAYMENT_REQUIRED", "detail": exc.detail, "reason": exc.reason},
        )
    return gate

def _build_orchestrator(db: Session, settings: Settings, audit: AuditService, workspace_id: str, app: Any = None) -> RunOrchestrator:
    pgl = create_pgl_client(db=db, settings=settings, use_veklom=True)
    signer = create_enterprise_signer_from_settings(settings)
    builder = ExecutionIdentityBuilder(signer=signer)
    executor = build_executor(settings, db=db, workspace_id=workspace_id, app=app)
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
        runtime_kind=settings.runtime_kind,
        runtime_instance=settings.runtime_instance,
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
                "detail": str(exc),
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
    except ProviderRateLimitedError as exc:
        db.commit()
        headers = {}
        if exc.retry_after:
            headers["Retry-After"] = str(exc.retry_after)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "PROVIDER_RATE_LIMITED",
                "detail": str(exc),
                "retryable": True,
            },
            headers=headers,
        )
    except (ProviderCredentialRejectedError, ProviderPolicyRejectedError) as exc:
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={
                "error": getattr(exc, "error_code", "PROVIDER_ERROR"),
                "detail": str(exc),
                "terminal": True,
            },
        )
    except TerminalExecutionError as exc:
        db.commit()
        raise HTTPException(
            status_code=403,
            detail={
                "error": getattr(exc, "error_code", "EXECUTION_AUTHORITY_DENIED"),
                "detail": str(exc),
                "terminal": True,
            },
        )
    except RuntimeOwnershipError as exc:
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "RUNTIME_OWNERSHIP_CONFLICT",
                "detail": str(exc),
                "fail_stop": True,
                "retryable": False,
            },
        )
    except ExecutorUnavailableError as exc:
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "PROVIDER_UNAVAILABLE",
                "detail": str(exc),
                "retryable": True,
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


async def _verify_exec_request_integrity(request: Request, public_key: str) -> None:
    """Fail closed before authority if the signed /v1/exec message was altered.

    RFC 9421 establishes the authenticity and integrity of the transmitted
    request.  It deliberately does not produce, replace, or widen a CAPPO
    authority decision.  ``Request.body()`` is cached by Starlette, so this
    verification uses the same bytes FastAPI parsed into ``ExecRequest``.
    """
    try:
        verify_rfc9421_request(
            method=request.method,
            target_uri=str(request.url),
            headers=request.headers,
            body=await request.body(),
            public_key_hex=public_key,
        )
    except SignatureVerificationError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "HTTP_MESSAGE_INTEGRITY_INVALID",
                "detail": str(exc),
                "terminal": True,
            },
        ) from exc


def _build_capi_payload(body: ExecRequest) -> dict[str, Any]:
    """Build the signed cAPI intent without recursively hashing its signature."""
    return {
        "action": body.action or "execute",
        "data": body.model_dump(exclude={"security"}),
        "security": body.security,
    }


def _eee_builder(settings: Settings) -> EEEBuilder:
    """Use CAPPO's published beacon signing identity for EEE records."""
    return EEEBuilder(
        signing_key=settings.ei_signing_key,
        issuer=settings.capability_beacon_issuer,
        kid=settings.capability_beacon_kid,
    )


async def _seal_terminal_eee(
    *,
    orchestrator: RunOrchestrator,
    run: Any,
    result: dict[str, Any] | None,
    capi_evidence: dict[str, Any],
    builder: EEEBuilder,
) -> dict[str, Any]:
    """Bind a terminal CAPPO run to one signed EEE and the existing PGL seal.

    This is evidence persistence only. It receives a completed or denied run
    from the sole governed execution path and cannot make an authorization,
    select a provider, or trigger execution.
    """
    from cappo_backend.core.capi_pipeline import seal_evidence_pack

    envelope = build_terminal_eee(run, result=result, builder=builder)
    committed_result = result if result is not None else {"status": "denied"}
    seal = await seal_evidence_pack(
        envelope["envelope_hash"],
        committed_result,
        request_evidence=capi_evidence,
    )
    seal["eee"] = envelope
    orchestrator.record_evidence_seal(run, seal)
    return envelope

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

    test_only_echo = settings.environment.lower() == 'test' and settings.executor_mode == 'echo'
    # RTV-1 WID Enforcement
    if not test_only_echo:
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body).hexdigest()
        
        def _get_b64_json(headers: dict, key: str) -> dict | None:
            val = headers.get(key)
            if not val:
                return None
            try:
                return json.loads(base64.b64decode(val).decode('utf-8'))
            except Exception:
                return None
                
        wit_payload = _get_b64_json(request.headers, 'Workload-Identity')
        ect_payload = _get_b64_json(request.headers, 'Execution-Context')
        wpt_payload = _get_b64_json(request.headers, 'Workload-Proof')
        authority_payload = _get_b64_json(request.headers, 'Veklom-Authority')

    if hasattr(request.app.state, 'redis_client') and request.app.state.redis_client:
        replay_cache = RedisReplayCache(request.app.state.redis_client)
    else:
        class MockReplayCache(ReplayCache):
            def check_and_store(self, jti: str, expires_at: int) -> bool:
                return True
        replay_cache = MockReplayCache()

    if not test_only_echo:
        wid_validator = IdentityValidator('https://cappo.veklom.com', replay_cache)
        wid_middleware = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, wid_validator)
        
        try:
            wid_middleware.enforce(
                route='/v1/exec',
                method=request.method,
                trace_id=request.headers.get('x-request-id', 'unknown'),
                htu=request.url.path,
                body_hash=body_hash,
                wit_payload=wit_payload,
                ect_payload=ect_payload,
                wpt_payload=wpt_payload,
                authority_payload=authority_payload
            )
            
            if authority_payload:
                enforcer = CappoPreauthorizationEnforcer(replay_cache)
                wit = WorkloadIdentityToken(**wit_payload) if wit_payload else None
                ect = ExecutionContextToken(**ect_payload) if ect_payload else None
                wpt = WorkloadProofToken(**wpt_payload) if wpt_payload else None
                
                auth_kwargs = dict(authority_payload)
                if '_mock_hash' in auth_kwargs:
                    del auth_kwargs['_mock_hash']
                auth = AuthorityArtifact(**auth_kwargs)
                
                expected_auth_hash = hashlib.sha256(json.dumps(authority_payload, sort_keys=True).encode()).hexdigest()
                expected_ect_hash = hashlib.sha256(json.dumps(ect_payload, sort_keys=True).encode()).hexdigest() if ect_payload else ''
                expected_wit_hash = hashlib.sha256(json.dumps(wit_payload, sort_keys=True).encode()).hexdigest() if wit_payload else ''
                
                enforcer.authorize_consequence(
                    route='/v1/exec',
                    method=request.method,
                    trace_id=request.headers.get('x-request-id', 'unknown'),
                    request_target_hash='target_hash',
                    request_body_hash=body_hash,
                    requested_right=body.action or 'execute',
                    wit=wit,
                    ect=ect,
                    wpt=wpt,
                    authority=auth,
                    profile_id_only=False,
                    api_key_only=False,
                    expected_authority_hash=expected_auth_hash,
                    expected_ect_hash=expected_ect_hash,
                    expected_wit_hash=expected_wit_hash,
                    expected_scope_hash=auth.scope_hash,
                    expected_policy_decision_hash=auth.policy_decision_hash
                )
                
        except IdentityValidationError as e:
            return JSONResponse(status_code=403, content=e.to_evidence())
        except CappoAuthorizationError as e:
            return JSONResponse(status_code=403, content=e.to_evidence())


    # cAPI PHASE 1: Gatekeeper Enforcement
    from cappo_backend.core.capi_pipeline import enforce_capi_pipeline
    test_only_echo = settings.environment.lower() == "test" and settings.executor_mode == "echo"
    capi_public_key = "" if test_only_echo else _resolve_capi_gatekeeper_public_key(settings, body)

    # RFC 9421 request verification precedes all identity, payment, routing,
    # and semantic-authority work. A valid signature is only an integrity
    # assertion; the CAPPO pipeline below remains the consequence authority.
    if not test_only_echo:
        await _verify_exec_request_integrity(request, capi_public_key)
    
    # We construct the payload expected by cAPI
    capi_payload = _build_capi_payload(body)
    
    # Run the strict cAPI pipeline (Phases 1-6)
    if test_only_echo:
        capi_result = {"evidence_id": "test-only"}
    else:
        try:
            capi_result = await enforce_capi_pipeline(body.pgl_id or "unknown", capi_payload, capi_public_key)
        except Exception as e:
            # If security fails, we don't even reach orchestration
            raise HTTPException(status_code=401, detail=f"cAPI Gatekeeper Reject: {str(e)}")

    # P0-1: Canonical workspace is established from the authenticated scope only.
    # body.workspace_id is an optional hint; it may never choose or override authority.
    canonical_workspace = request.scope.get("auth_workspace")
    if not canonical_workspace:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "WORKSPACE_CONTEXT_MISSING",
                "detail": (
                    "No authenticated workspace context. The credential must resolve "
                    "to a workspace before execution begins."
                ),
            },
        )

    # If the caller supplied workspace_id in the body, it must agree with the
    # authenticated canonical workspace. It may never override it.
    body_workspace = getattr(body, "workspace_id", None)
    if body_workspace and body_workspace != "default" and body_workspace != canonical_workspace:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "WORKSPACE_MISMATCH",
                "detail": (
                    "Request body workspace_id does not match the authenticated workspace. "
                    "The credential determines workspace; callers may not override it."
                ),
            },
        )

    gate = _check_payment(db, canonical_workspace, body.action_cost_cents, settings, request.app)
    orchestrator = _build_orchestrator(db, settings, audit, workspace_id=canonical_workspace, app=request.app)
    try:
        payload = body.model_dump()
        payload["workspace_id"] = canonical_workspace
        result = _execute_run(orchestrator, payload, db)
        if result and "tokens" in result and isinstance(result["tokens"], int):
            gate.record_tokens(canonical_workspace, result["tokens"])
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        run = orchestrator.last_run
        terminal_after_admission = {
            "EXECUTION_IDENTITY_REQUIRED",
            "CAPPO_GOVERNANCE_DENIED",
            "EXECUTION_AUTHORITY_DENIED",
            "RUNTIME_OWNERSHIP_CONFLICT",
            "PROVIDER_UNAVAILABLE",
            "AUTHORITY_CONTEXT_MISSING",
            "PROVIDER_NOT_AUTHORIZED",
            "AUTHORIZED_PROVIDER_NOT_CONFIGURED",
            "PROVIDER_CREDENTIAL_REJECTED",
            "PROVIDER_POLICY_REJECTED",
            "LOCAL_AUTHORIZER_UNAVAILABLE",
            "PROVIDER_RATE_LIMITED",
        }
        if (
            not test_only_echo
            and detail.get("error") in terminal_after_admission
            and run is not None
            and (run.pgl_identity or {}).get("pre_execution_certificate_id")
        ):
            await _seal_terminal_eee(
                orchestrator=orchestrator,
                run=run,
                result=None,
                capi_evidence=capi_result["evidence"],
                builder=_eee_builder(settings),
            )
            db.commit()
        raise
    finally:
        gate.decrement_concurrent()

    run = orchestrator.last_run
    if not test_only_echo:
        if run is None:
            raise RuntimeError("governed execution completed without a run record")
        await _seal_terminal_eee(
            orchestrator=orchestrator,
            run=run,
            result=result,
            capi_evidence=capi_result["evidence"],
            builder=_eee_builder(settings),
        )
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
        links={
            "audit": {"href": f"/api/v1/gpc/audit/{run.run_id if run else 'unknown'}", "method": "GET"},
            "stake": {"href": "/api/v1/vnp/stake", "method": "POST"},
            "evidence": {"href": "/api/v1/evidence/verify", "method": "POST"}
        }
    )
