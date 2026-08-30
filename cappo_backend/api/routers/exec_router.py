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
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cappo_backend.api.routers.capability_mount_router import get_registry
from cappo_backend.authorization.cappo_auth import CappoPreauthorizationEnforcer
from cappo_backend.authorization.errors import CappoAuthorizationError
from cappo_backend.capability_mount.models import Decision
from cappo_backend.capability_mount.service import MountRegistry
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


class CapabilityLeaseInput(BaseModel):
    mount_id: str
    token_id: str
    nonce: str
    approval_token: str | None = None
    suppression_evidence: str | None = None


class TargetPrecondition(BaseModel):
    target_id: str
    expected_state_hash: str
    observed_state_hash: str
    observed_at: str
    signature: str


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
    capability_lease: CapabilityLeaseInput | None = None
    target_precondition: TargetPrecondition | None = None


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
    capability_lease: dict[str, Any] | None = None


def _check_payment(
    db: Session,
    workspace_id: str,
    cost_cents: int,
    settings: Settings,
    app: Any = None,
) -> PaymentGate:
    redis_client = (
        getattr(app.state, "redis_client", None) if app and hasattr(app, "state") else None
    )
    gate = PaymentGate(db, redis_client=redis_client, settings=settings)
    try:
        gate.check(workspace_id, cost_cents=cost_cents)
    except PaymentRequiredError as exc:
        raise HTTPException(
            status_code=402,
            detail={"error": "PAYMENT_REQUIRED", "detail": exc.detail, "reason": exc.reason},
        )
    return gate


def _build_orchestrator(
    db: Session,
    settings: Settings,
    audit: AuditService,
    workspace_id: str,
    app: Any = None,
) -> RunOrchestrator:
    pgl = create_pgl_client(db=db, settings=settings, use_veklom=True)
    signer = create_enterprise_signer_from_settings(settings)
    builder = ExecutionIdentityBuilder(signer=signer)
    executor = build_executor(settings, db=db, workspace_id=workspace_id, app=app)
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
    orchestrator: RunOrchestrator, payload: dict[str, Any], db: Session
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
                "detail": "cAPI Gatekeeper requires CAPI_GATEKEEPER_PUBLIC_KEY to be configured.",
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
        "data": body.model_dump(exclude={"security"}),
        "security": body.security,
    }


def _eee_builder(settings: Settings) -> EEEBuilder:
    return EEEBuilder(
        signing_key=settings.ei_signing_key,
        issuer=settings.capability_beacon_issuer,
        kid=settings.capability_beacon_kid,
    )


def _verify_target_precondition(
    precondition: TargetPrecondition, settings: Settings
) -> None:
    """Verify the independent target observation before any governed write."""
    if not settings.vnp_federation_public_key:
        raise HTTPException(
            status_code=503,
            detail={"error": "TARGET_OBSERVER_KEY_UNAVAILABLE", "fail_closed": True},
        )
    observation = {
        "target_id": precondition.target_id,
        "observed_state_hash": precondition.observed_state_hash,
        "observed_at": precondition.observed_at,
    }
    canonical = json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(settings.vnp_federation_public_key)
        )
        public_key.verify(bytes.fromhex(precondition.signature), canonical)
    except (InvalidSignature, ValueError) as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "TARGET_OBSERVATION_SIGNATURE_INVALID", "fail_closed": True},
        ) from exc
    try:
        observed_at = datetime.fromisoformat(precondition.observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "TARGET_OBSERVATION_TIME_INVALID", "fail_closed": True},
        ) from exc
    now = datetime.now(UTC)
    if (
        observed_at.tzinfo is None
        or observed_at < now - timedelta(seconds=settings.capability_beacon_ttl_seconds)
        or observed_at > now + timedelta(seconds=30)
    ):
        raise HTTPException(
            status_code=409,
            detail={"error": "TARGET_OBSERVATION_EXPIRED", "fail_closed": True},
        )
    if precondition.expected_state_hash != precondition.observed_state_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "STALE_TARGET",
                "target_id": precondition.target_id,
                "expected_state_hash": precondition.expected_state_hash,
                "observed_state_hash": precondition.observed_state_hash,
            },
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


@router.post("/exec", response_model=ExecResponse)
async def governed_exec(
    body: ExecRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    mount_registry: MountRegistry = Depends(get_registry),
) -> ExecResponse:
    """Single governed execution entry path (Option A)."""
    start = time.monotonic()
    audit = AuditService(db)
    test_only_echo = settings.environment.lower() == "test" and settings.executor_mode == "echo"

    if not test_only_echo:
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body).hexdigest()

        def _get_b64_json(headers: Any, key: str) -> dict[str, Any] | None:
            val = headers.get(key)
            if not val:
                return None
            try:
                return json.loads(base64.b64decode(val).decode("utf-8"))
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
        wid_middleware = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, wid_validator)
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
                    hashlib.sha256(json.dumps(ect_payload, sort_keys=True).encode()).hexdigest()
                    if ect_payload
                    else ""
                )
                expected_wit_hash = (
                    hashlib.sha256(json.dumps(wit_payload, sort_keys=True).encode()).hexdigest()
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

    capi_public_key = "" if test_only_echo else _resolve_capi_gatekeeper_public_key(settings, body)
    if not test_only_echo:
        await _verify_exec_request_integrity(request, capi_public_key)
    capi_payload = _build_capi_payload(body)
    if test_only_echo:
        capi_result = {"evidence_id": "test-only"}
    else:
        try:
            capi_result = await enforce_capi_pipeline(
                body.pgl_id or "unknown", capi_payload, capi_public_key
            )
        except Exception as exc:
            raise HTTPException(
                status_code=401, detail=f"cAPI Gatekeeper Reject: {str(exc)}"
            ) from exc

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

    if body.target_precondition is not None:
        _verify_target_precondition(body.target_precondition, settings)

    lease_result: dict[str, Any] | None = None
    mount_record = None
    gate: PaymentGate | None = None
    if body.capability_lease is not None:
        principal = request.scope.get("auth_principal")
        if not isinstance(principal, str) or not principal:
            raise HTTPException(status_code=401, detail={"error": "AUTHENTICATION_REQUIRED"})
        if not body.action:
            raise HTTPException(
                status_code=403,
                detail={"error": "CAPABILITY_LEASE_DENIED", "reason": "action_required"},
            )
        mount_record, _mount_state = mount_registry.status(
            body.capability_lease.mount_id,
            owner_principal=principal,
            owner_workspace=canonical_workspace,
        )
        if mount_record is not None and body.target_precondition is not None:
            authorized_target = mount_record.token.scope.project
            if body.target_precondition.target_id != authorized_target:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "TARGET_SCOPE_MISMATCH",
                        "authorized_target": authorized_target,
                        "observed_target": body.target_precondition.target_id,
                        "fail_closed": True,
                    },
                )
        if mount_record is not None and (
            mount_record.token.nonce_consumed
            or body.capability_lease.token_id != mount_record.token.token_id
            or body.capability_lease.nonce != mount_record.token.nonce
        ):
            _decision, reason, _anchor, _ = mount_registry.evaluate(
                body.capability_lease.mount_id,
                body.action,
                token_id=body.capability_lease.token_id,
                nonce=body.capability_lease.nonce,
                owner_principal=principal,
                owner_workspace=canonical_workspace,
            )
            db.commit()
            raise HTTPException(
                status_code=403,
                detail={"error": "CAPABILITY_LEASE_DENIED", "reason": reason},
            )
        if (
            mount_record is not None
            and body.action in mount_record.token.grants.writes
            and body.target_precondition is None
        ):
            raise HTTPException(
                status_code=428,
                detail={
                    "error": "TARGET_PRECONDITION_REQUIRED",
                    "action": body.action,
                    "fail_closed": True,
                },
            )
        gate = _check_payment(
            db, canonical_workspace, body.action_cost_cents, settings, request.app
        )
        decision, reason, anchor, _ = mount_registry.evaluate(
            body.capability_lease.mount_id,
            body.action,
            token_id=body.capability_lease.token_id,
            nonce=body.capability_lease.nonce,
            owner_principal=principal,
            owner_workspace=canonical_workspace,
            approval_token=body.capability_lease.approval_token,
            suppression_evidence=body.capability_lease.suppression_evidence,
        )
        if decision is not Decision.ALLOW:
            gate.decrement_concurrent()
            db.commit()
            raise HTTPException(
                status_code=403,
                detail={"error": "CAPABILITY_LEASE_DENIED", "reason": reason},
            )
        lease_result = {
            "mount_id": body.capability_lease.mount_id,
            "decision": decision.value,
            "reason": reason,
            "anchor_id": anchor.anchor_id,
        }

    if gate is None:
        gate = _check_payment(
            db, canonical_workspace, body.action_cost_cents, settings, request.app
        )
    orchestrator = _build_orchestrator(
        db, settings, audit, workspace_id=canonical_workspace, app=request.app
    )
    try:
        payload = body.model_dump()
        payload["workspace_id"] = canonical_workspace
        if body.capability_lease is not None and mount_record is not None:
            payload["execution_id"] = mount_record.token.execution_id
            payload["capability_authority"] = {
                **(lease_result or {}),
                "action": body.action,
                "execution_id": mount_record.token.execution_id,
                "expires_at": mount_record.token.expires_at.isoformat(),
            }
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
    execution_id = (run.execution_identity or {}).get("execution_id") if run else None
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
        links={
            "audit": {
                "href": f"/api/v1/gpc/audit/{run.run_id if run else 'unknown'}",
                "method": "GET",
            },
            "stake": {"href": "/api/v1/vnp/stake", "method": "POST"},
            "evidence": {"href": "/api/v1/evidence/verify", "method": "POST"},
        },
        capability_lease=lease_result,
    )
