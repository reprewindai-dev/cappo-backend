"""Governed /v1/exec route (Option A — routes through orchestrator).

Migration note §5 / EI Plan §/v1/exec migration: replaces the old ungoverned
``POST /v1/exec`` with a single governed entry path that inherits governance,
PGL, EI minting, execution, and attestation from the orchestrator. Preserves
the ``ExecResponse`` contract (response/model/provider/tokens/latency/log_id/conversation_id).

There is deliberately no alternate execution path; the old public-allowlist
bypass is gone.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.api.routers.capability_mount_router import get_registry
from cappo_backend.capability_mount.models import Decision
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import get_session
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.models.vnp_models import APIState, ProbeEvent, RegionalTelemetry
from cappo_backend.security.http_signatures import (
    SignatureVerificationError,
    verify_rfc9421_request,
)
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.capability_beacon import active_signing_seed
from cappo_backend.services.eee import (
    EEEBuilder,
    EEEVerifier,
    VerificationVerdict,
    build_terminal_eee,
)
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder
from cappo_backend.services.enterprise_signer import create_enterprise_signer_from_settings
from cappo_backend.services.executor import (
    ExecutorUnavailableError,
    ProviderCredentialRejectedError,
    ProviderPolicyRejectedError,
    ProviderRateLimitedError,
    TerminalExecutionError,
)
from cappo_backend.services.gnomledger_pgl_client import GnomledgerPGLClient
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


class ExecutionEvidenceResponse(BaseModel):
    execution_id: str
    proof_state: str
    verification_reasons: list[str]
    eee: dict[str, Any]
    pgl: dict[str, Any]


class ExecutionMeasurementResponse(BaseModel):
    execution_id: str
    proof_state: str
    vnp_api_did: str
    provider: str
    resulting_state: dict[str, Any]
    observations: list[dict[str, Any]]
    aggregates: list[dict[str, Any]]


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
        signing_key=active_signing_seed(settings),
        issuer=settings.capability_beacon_issuer,
        kid=settings.capability_beacon_kid,
    )


def _verify_target_precondition(precondition: TargetPrecondition, settings: Settings) -> None:
    """Verify an independently signed target observation, then enforce equality."""
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
    if observed_at.tzinfo is None or observed_at < now - timedelta(
        seconds=settings.capability_beacon_ttl_seconds
    ) or observed_at > now + timedelta(seconds=30):
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


@router.get("/executions/{execution_id}/evidence", response_model=ExecutionEvidenceResponse)
def get_execution_evidence(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ExecutionEvidenceResponse:
    """Return the signed EEE and exact persisted PGL link for one execution."""
    workspace_id = request.scope.get("auth_workspace")
    run = db.get(GovernedRun, execution_id)
    if run is None or not workspace_id or run.workspace_id != workspace_id:
        raise HTTPException(
            status_code=404,
            detail={"error": "EVIDENCE_NOT_FOUND", "execution_id": execution_id},
        )

    event_id = (run.pgl_identity or {}).get("capi_evidence_event_id")
    event = db.get(PGLLedgerEvent, event_id) if isinstance(event_id, str) else None
    remote_event: dict[str, Any] | None = None
    if event is None and isinstance(event_id, str) and settings.gnomledger_url:
        try:
            remote_event = GnomledgerPGLClient(settings).get_ledger_event(event_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "EVIDENCE_NOT_FOUND", "execution_id": execution_id},
                ) from exc
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "EVIDENCE_STORE_UNAVAILABLE",
                    "execution_id": execution_id,
                    "fail_closed": True,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "EVIDENCE_STORE_UNAVAILABLE",
                    "execution_id": execution_id,
                    "fail_closed": True,
                },
            ) from exc
    remote_details = remote_event.get("details") if isinstance(remote_event, dict) else None
    seal = (
        event.payload.get("evidence_seal")
        if event is not None
        else remote_details.get("evidence_seal")
        if isinstance(remote_details, dict)
        else None
    )
    envelope = seal.get("eee") if isinstance(seal, dict) else None
    is_local_seal = event is not None and event.event_type == "capi_evidence_sealed"
    is_remote_seal = (
        isinstance(remote_event, dict)
        and remote_event.get("event_type") == "custom"
        and isinstance(remote_details, dict)
        and remote_details.get("semantic_event_type") == "capi_evidence_sealed"
    )
    if not (is_local_seal or is_remote_seal) or not isinstance(envelope, dict):
        raise HTTPException(
            status_code=404,
            detail={"error": "EVIDENCE_NOT_FOUND", "execution_id": execution_id},
        )

    builder = _eee_builder(settings)
    report = EEEVerifier({settings.capability_beacon_kid: builder.public_key_bytes}).verify(envelope)
    if envelope.get("execution_id") != execution_id:
        raise HTTPException(
            status_code=409,
            detail={"error": "EVIDENCE_EXECUTION_MISMATCH", "execution_id": execution_id},
        )
    proof_state = {
        VerificationVerdict.VALID: "verified",
        VerificationVerdict.VALID_WITH_UNRESOLVED_REFS: "verified_with_unresolved_refs",
        VerificationVerdict.INVALID: "failed",
        VerificationVerdict.UNSUPPORTED_VERSION: "unknown",
    }[report.verdict]
    return ExecutionEvidenceResponse(
        execution_id=execution_id,
        proof_state=proof_state,
        verification_reasons=report.reasons,
        eee=envelope,
        pgl={
            "event_id": event.event_id if event is not None else remote_event["event_id"],
            "certificate_id": (
                event.certificate_id
                if event is not None
                else remote_details.get("certificate_id")
            ),
            "event_hash": event.event_hash if event is not None else remote_event.get("event_hash"),
            "previous_event_hash": (
                event.previous_event_hash
                if event is not None
                else remote_event.get("prev_event_hash")
            ),
            "persisted": True,
            "created_at": (
                event.created_at.isoformat()
                if event is not None
                else remote_event.get("created_at")
            ),
        },
    )


@router.get("/executions/{execution_id}/measurements", response_model=ExecutionMeasurementResponse)
def get_execution_measurements(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> ExecutionMeasurementResponse:
    """Return independently signed VNP observations associated with the executed provider."""
    workspace_id = request.scope.get("auth_workspace")
    run = db.get(GovernedRun, execution_id)
    if run is None or not workspace_id or run.workspace_id != workspace_id:
        raise HTTPException(
            status_code=404,
            detail={"error": "MEASUREMENT_NOT_FOUND", "execution_id": execution_id},
        )

    provider = (run.result_payload or {}).get("provider")
    execution_probes = db.execute(
        select(ProbeEvent)
        .where(ProbeEvent.payload_json["execution_id"].as_string() == execution_id)
        .order_by(ProbeEvent.created_at.desc())
    ).scalars().all()
    api_ids = {probe.api_id for probe in execution_probes}
    api = db.get(APIState, next(iter(api_ids))) if len(api_ids) == 1 else None
    if api is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "MEASUREMENT_NOT_FOUND", "execution_id": execution_id},
        )

    result_state_hash = sha256_json(run.result_payload or {})
    stale_probes = [
        probe
        for probe in execution_probes
        if (probe.payload_json or {}).get("result_state_hash") != result_state_hash
    ]
    if stale_probes:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "STALE_RESULT_OBSERVATION",
                "execution_id": execution_id,
                "expected_result_state_hash": result_state_hash,
            },
        )
    probes = execution_probes
    telemetry = db.execute(
        select(RegionalTelemetry)
        .where(RegionalTelemetry.api_id == api.id)
        .order_by(RegionalTelemetry.measured_at.desc())
    ).scalars().all()
    if not probes or not telemetry:
        raise HTTPException(
            status_code=404,
            detail={"error": "MEASUREMENT_NOT_FOUND", "execution_id": execution_id},
        )

    return ExecutionMeasurementResponse(
        execution_id=execution_id,
        proof_state="verified",
        vnp_api_did=api.api_did,
        provider=provider,
        resulting_state={"hash": result_state_hash, "independently_observed": True},
        observations=[
            {
                "probe_id": str(probe.id),
                "worker_id": probe.worker_id,
                "region": probe.region,
                "latency_ms": probe.latency_ms,
                "status_code": probe.status_code,
                "signature": probe.signature,
                "payload": probe.payload_json,
                "observed_at": probe.created_at.isoformat(),
            }
            for probe in probes
        ],
        aggregates=[
            {
                "region": row.region,
                "p50_latency_ms": row.p50_latency_ms,
                "p95_latency_ms": row.p95_latency_ms,
                "p99_latency_ms": row.p99_latency_ms,
                "error_rate_percent": float(row.error_rate_percent),
                "uptime_percent": float(row.uptime_percent),
                "throughput_rps": row.throughput_rps,
                "measured_at": row.measured_at.isoformat(),
            }
            for row in telemetry
        ],
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
    mount_registry: MountRegistry = Depends(get_registry),
) -> ExecResponse:
    """Single governed execution entry path (Option A)."""
    start = time.monotonic()
    audit = AuditService(db)

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

    if body.target_precondition is not None:
        _verify_target_precondition(body.target_precondition, settings)

    lease_result: dict[str, Any] | None = None
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
            decision, reason, _anchor, _ = mount_registry.evaluate(
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
        # Commercial/rate admission must succeed before the single-use lease
        # is irreversibly consumed.
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
        gate = _check_payment(db, canonical_workspace, body.action_cost_cents, settings, request.app)
    orchestrator = _build_orchestrator(db, settings, audit, workspace_id=canonical_workspace, app=request.app)
    try:
        payload = body.model_dump()
        payload["workspace_id"] = canonical_workspace
        if body.capability_lease is not None and mount_record is not None:
            payload["execution_id"] = mount_record.token.execution_id
            payload["capability_authority"] = {
                **lease_result,
                "action": body.action,
                "execution_id": mount_record.token.execution_id,
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
            "evidence": {"href": f"/v1/executions/{execution_id}/evidence", "method": "GET"},
            "measurements": {
                "href": f"/v1/executions/{execution_id}/measurements",
                "method": "GET",
            },
        },
        capability_lease=lease_result,
    )
