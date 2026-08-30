"""Governed /v1/exec route — the sole public consequence boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from cappo_backend.api.routers.capability_mount_router import get_registry
from cappo_backend.authorization.cappo_auth import CappoPreauthorizationEnforcer
from cappo_backend.authorization.errors import CappoAuthorizationError
from cappo_backend.capability_mount.models import Decision
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
from cappo_backend.models.consequence_execution import build_intent_hash
from cappo_backend.security.http_signatures import (
    SignatureVerificationError,
    verify_rfc9421_request,
)
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.consequence_lifecycle import ConsequenceLifecycleExecutor
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


class CapabilityLeaseRef(BaseModel):
    """Proof-of-possession handle for an already persisted CAPPO mount."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mount_id: str = Field(min_length=1)
    token_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)


class ExecRequest(BaseModel):
    prompt: str
    agent_id: str | None = None
    pgl_id: str | None = None
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
    capability_lease: CapabilityLeaseRef | None = Field(default=None, exclude=True)


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
    capability_lease: dict[str, Any] | None = None
    links: dict[str, Any] | None = None


class _LifecycleContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    receipt_id: str
    operation_id: str
    intent_hash: str
    resource: str


def _check_payment(
    db: Session,
    workspace_id: str,
    cost_cents: int,
    settings: Settings,
    app: Any = None,
) -> PaymentGate:
    redis_client = (
        getattr(app.state, "redis_client", None)
        if app and hasattr(app, "state")
        else None
    )
    gate = PaymentGate(db, redis_client=redis_client, settings=settings)
    try:
        gate.check(workspace_id, cost_cents=cost_cents)
    except PaymentRequiredError as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "PAYMENT_REQUIRED",
                "detail": exc.detail,
                "reason": exc.reason,
            },
        )
    return gate


def _build_orchestrator(
    db: Session,
    settings: Settings,
    audit: AuditService,
    workspace_id: str,
    app: Any = None,
    lifecycle: _LifecycleContext | None = None,
) -> RunOrchestrator:
    pgl = create_pgl_client(db=db, settings=settings, use_veklom=True)
    signer = create_enterprise_signer_from_settings(settings)
    builder = ExecutionIdentityBuilder(signer=signer)
    executor = build_executor(settings, db=db, workspace_id=workspace_id, app=app)
    if lifecycle is not None:
        executor = ConsequenceLifecycleExecutor(
            db=db,
            delegate=executor,
            receipt_id=lifecycle.receipt_id,
            operation_id=lifecycle.operation_id,
            intent_hash=lifecycle.intent_hash,
            resource=lifecycle.resource,
        )

    revocation = RevocationService(db, audit)
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


def _execute_run(
    orchestrator: RunOrchestrator,
    payload: dict[str, Any],
    db: Session,
) -> dict[str, Any]:
    try:
        return orchestrator.run_governed(payload)
    except EIValidationError as exc:
        db.commit()
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
        db.commit()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "CAPPO_GOVERNANCE_DECISION_REQUIRED",
                "detail": str(exc),
                "fail_closed": True,
            },
        )
    except GovernanceDeniedError as exc:
        db.commit()
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
                "detail": (
                    "cAPI Gatekeeper requires CAPI_GATEKEEPER_PUBLIC_KEY "
                    "to be configured."
                ),
            },
        )
    return public_key


async def _verify_exec_request_integrity(request: Request, public_key: str) -> None:
    try:
        verify_rfc9421_request(
            method=request.method,
            target_uri=str(request.url),
            headers=request.headers,
            body=await request.body(),
            public_key_hex=public_key,
            required_header_components={
                "workload-identity",
                "execution-context",
                "workload-proof",
                "veklom-authority",
                "x-veklom-actor",
                "x-veklom-nonce",
            },
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
    return {
        "action": body.action or "execute",
        "data": body.model_dump(exclude={"security", "capability_lease"}),
        "security": body.security,
    }


def _eee_builder(settings: Settings) -> EEEBuilder:
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


def _resolve_capability_lease(
    *,
    body: ExecRequest,
    request: Request,
    db: Session,
    workspace_id: str,
):
    lease_ref = body.capability_lease
    if lease_ref is None:
        return None
    principal = request.scope.get("auth_principal")
    if not isinstance(principal, str) or not principal:
        raise HTTPException(
            status_code=401,
            detail={"error": "AUTHENTICATION_REQUIRED"},
        )

    registry = get_registry(request, db)
    record, state = registry.status(
        lease_ref.mount_id,
        owner_principal=principal,
        owner_workspace=workspace_id,
    )
    if record is None or state != "mounted":
        raise HTTPException(
            status_code=403,
            detail={"error": "CAPABILITY_LEASE_NOT_ACTIVE", "detail": state},
        )
    if (
        record.token.scope.workspace != workspace_id
        or record.mount.scope.workspace != workspace_id
    ):
        raise HTTPException(
            status_code=403,
            detail={"error": "WORKSPACE_SCOPE_MISMATCH"},
        )
    if not hmac.compare_digest(record.token.token_id, lease_ref.token_id):
        raise HTTPException(
            status_code=403,
            detail={"error": "CAPABILITY_PROOF_INVALID"},
        )
    if not hmac.compare_digest(record.token.nonce, lease_ref.nonce):
        raise HTTPException(
            status_code=403,
            detail={"error": "CAPABILITY_PROOF_INVALID"},
        )
    if not hmac.compare_digest(record.token.execution_id, lease_ref.execution_id):
        raise HTTPException(
            status_code=403,
            detail={"error": "CAPABILITY_EXECUTION_ID_MISMATCH"},
        )
    if not record.token.biscuit_token:
        raise HTTPException(
            status_code=403,
            detail={"error": "CRYPTOGRAPHIC_AUTHORITY_REQUIRED"},
        )

    action = body.action or "execute"
    allowed = set(
        record.token.grants.reads
        + record.token.grants.writes
        + record.token.grants.external_send
    )
    if action not in allowed:
        raise HTTPException(
            status_code=403,
            detail={"error": "CAPABILITY_ACTION_OUT_OF_SCOPE", "action": action},
        )
    return registry, record, principal


@router.post("/exec", response_model=ExecResponse)
async def governed_exec(
    body: ExecRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ExecResponse:
    start = time.monotonic()
    audit = AuditService(db)
    test_only_echo = (
        settings.environment.lower() == "test" and settings.executor_mode == "echo"
    )

    if not test_only_echo:
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body).hexdigest()

        def _get_b64_json(headers: Any, key: str) -> dict | None:
            value = headers.get(key)
            if not value:
                return None
            try:
                return json.loads(base64.b64decode(value).decode("utf-8"))
            except Exception:
                return None

        wit_payload = _get_b64_json(request.headers, "Workload-Identity")
        ect_payload = _get_b64_json(request.headers, "Execution-Context")
        wpt_payload = _get_b64_json(request.headers, "Workload-Proof")
        authority_payload = _get_b64_json(request.headers, "Veklom-Authority")

    if hasattr(request.app.state, "redis_client") and request.app.state.redis_client:
        replay_cache = RedisReplayCache(request.app.state.redis_client)
    else:

        class MockReplayCache(ReplayCache):
            def check_and_store(self, jti: str, expires_at: int) -> bool:
                return True

        replay_cache = MockReplayCache()

    if not test_only_echo:
        wid_validator = IdentityValidator("https://cappo.veklom.com", replay_cache)
        wid_middleware = WIDMiddlewareContext(
            RouteClassification.CONSEQUENCE,
            wid_validator,
        )
        try:
            wid_middleware.enforce(
                route="/v1/exec",
                method=request.method,
                trace_id=request.headers.get("x-request-id", "unknown"),
                htu=request.url.path,
                body_hash=body_hash,
                wit_payload=wit_payload,
                ect_payload=ect_payload,
                wpt_payload=wpt_payload,
                authority_payload=authority_payload,
            )
            if authority_payload:
                enforcer = CappoPreauthorizationEnforcer(replay_cache)
                wit = WorkloadIdentityToken(**wit_payload) if wit_payload else None
                ect = ExecutionContextToken(**ect_payload) if ect_payload else None
                wpt = WorkloadProofToken(**wpt_payload) if wpt_payload else None
                auth_kwargs = dict(authority_payload)
                auth_kwargs.pop("_mock_hash", None)
                auth = AuthorityArtifact(**auth_kwargs)
                expected_auth_hash = hashlib.sha256(
                    json.dumps(authority_payload, sort_keys=True).encode()
                ).hexdigest()
                expected_ect_hash = (
                    hashlib.sha256(
                        json.dumps(ect_payload, sort_keys=True).encode()
                    ).hexdigest()
                    if ect_payload
                    else ""
                )
                expected_wit_hash = (
                    hashlib.sha256(
                        json.dumps(wit_payload, sort_keys=True).encode()
                    ).hexdigest()
                    if wit_payload
                    else ""
                )
                enforcer.authorize_consequence(
                    route="/v1/exec",
                    method=request.method,
                    trace_id=request.headers.get("x-request-id", "unknown"),
                    request_target_hash="target_hash",
                    request_body_hash=body_hash,
                    requested_right=body.action or "execute",
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
                    expected_policy_decision_hash=auth.policy_decision_hash,
                )
        except IdentityValidationError as exc:
            return JSONResponse(status_code=403, content=exc.to_evidence())
        except CappoAuthorizationError as exc:
            return JSONResponse(status_code=403, content=exc.to_evidence())

    from cappo_backend.core.capi_pipeline import enforce_capi_pipeline

    capi_public_key = (
        "" if test_only_echo else _resolve_capi_gatekeeper_public_key(settings, body)
    )
    if not test_only_echo:
        await _verify_exec_request_integrity(request, capi_public_key)

    capi_payload = _build_capi_payload(body)
    if test_only_echo:
        capi_result = {"evidence_id": "test-only"}
    else:
        try:
            capi_result = await enforce_capi_pipeline(
                body.pgl_id or "unknown",
                capi_payload,
                capi_public_key,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=401,
                detail=f"cAPI Gatekeeper Reject: {str(exc)}",
            ) from exc

    canonical_workspace = request.scope.get("auth_workspace")
    if not canonical_workspace:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "WORKSPACE_CONTEXT_MISSING",
                "detail": (
                    "No authenticated workspace context. The credential must "
                    "resolve to a workspace before execution begins."
                ),
            },
        )
    body_workspace = body.workspace_id
    if (
        body_workspace
        and body_workspace != "default"
        and body_workspace != canonical_workspace
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "WORKSPACE_MISMATCH",
                "detail": (
                    "Request body workspace_id does not match the authenticated "
                    "workspace."
                ),
            },
        )

    lease_context = _resolve_capability_lease(
        body=body,
        request=request,
        db=db,
        workspace_id=str(canonical_workspace),
    )
    gate = _check_payment(
        db,
        str(canonical_workspace),
        body.action_cost_cents,
        settings,
        request.app,
    )
    lease_receipt_id: str | None = None
    lease_ref = body.capability_lease
    orchestrator: RunOrchestrator | None = None

    try:
        payload = body.model_dump(exclude={"capability_lease"})
        payload["workspace_id"] = canonical_workspace
        lifecycle: _LifecycleContext | None = None

        if lease_context is not None and lease_ref is not None:
            registry, record, principal = lease_context
            decision, reason, _anchor, detail = registry.evaluate(
                lease_ref.mount_id,
                body.action or "execute",
                token_id=lease_ref.token_id,
                nonce=lease_ref.nonce,
                owner_principal=principal,
                owner_workspace=str(canonical_workspace),
            )
            if decision is not Decision.ALLOW:
                raise HTTPException(
                    status_code=403,
                    detail={"error": "CAPABILITY_LEASE_DENIED", "reason": reason},
                )
            lease_receipt_id = (detail or {}).get("receipt_id")
            if not lease_receipt_id:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "CAPABILITY_RECEIPT_MISSING",
                        "fail_closed": True,
                    },
                )

            action = body.action or "execute"
            payload["directive"] = "ALLOW"
            payload["scope"] = {
                "tools": [action],
                "allowed_effects": [action],
            }
            payload["capability_mount_id"] = lease_ref.mount_id
            payload["capability_receipt_id"] = lease_receipt_id
            payload["capability_execution_id"] = record.token.execution_id
            payload["execution_id"] = record.token.execution_id

            operation_id = f"exec:{record.token.execution_id}"
            lifecycle = _LifecycleContext(
                receipt_id=lease_receipt_id,
                operation_id=operation_id,
                intent_hash=build_intent_hash(
                    mount_id=lease_ref.mount_id,
                    execution_id=record.token.execution_id,
                    action=action,
                    resource="provider-dispatch",
                    normalized_args={
                        "prompt_sha256": hashlib.sha256(
                            body.prompt.encode("utf-8")
                        ).hexdigest(),
                    },
                ),
                resource="provider-dispatch",
            )

        orchestrator = _build_orchestrator(
            db,
            settings,
            audit,
            workspace_id=str(canonical_workspace),
            app=request.app,
            lifecycle=lifecycle,
        )
        result = _execute_run(orchestrator, payload, db)
        if result and "tokens" in result and isinstance(result["tokens"], int):
            gate.record_tokens(str(canonical_workspace), result["tokens"])
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        run = orchestrator.last_run if orchestrator is not None else None
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

    if orchestrator is None:
        raise RuntimeError("governed execution did not construct an orchestrator")
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

    execution_id = (run.execution_identity or {}).get("execution_id") if run else None
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
        execution_id=execution_id,
        capability_lease=(
            {
                "mount_id": lease_ref.mount_id,
                "execution_id": lease_ref.execution_id,
                "receipt_id": lease_receipt_id,
                "decision": "allow",
                "nonce_consumed": True,
            }
            if lease_ref is not None and lease_receipt_id
            else None
        ),
        links={
            "audit": {
                "href": f"/api/v1/gpc/audit/{run.run_id if run else 'unknown'}",
                "method": "GET",
            },
            "evidence": {
                "href": f"/v1/executions/{execution_id or 'unknown'}/evidence",
                "method": "GET",
            },
            "measurements": {
                "href": f"/v1/executions/{execution_id or 'unknown'}/measurements",
                "method": "GET",
            },
        },
    )
