"""Capability package discovery and ephemeral mount lifecycle endpoints."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import ConsequenceContext, validate_resource
from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    EphemeralScopedToken,
    LifecycleState,
    Mount,
    MountPolicy,
    MountScope,
    UnmountReason,
)
from cappo_backend.capability_mount.service import (
    AnchorResult,
    LocalConfirmedAnchor,
    MountRegistry,
    UnconfirmedAnchor,
)
from cappo_backend.db.session import get_session, get_unscoped_session
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.entitlement_meter import (
    GOVERNED_ACTION,
    GOVERNED_EXECUTION,
    VERIFICATION_READ,
    EntitlementMeter,
    MeterOutcome,
    get_meter,
)
from cappo_backend.services.mount_evidence import BoundMountEvidenceVerifier
from cappo_backend.services.mount_pgl import AuditPGLAnchor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/capability", tags=["Capability Mount"])


class ActionScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reads: list[str] | None = None
    writes: list[str] | None = None
    blocked: list[str] = Field(default_factory=list)


class MountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_ref: str = Field(min_length=1)
    execution_scope: MountScope
    requested_action_scope: ActionScope = Field(default_factory=ActionScope)
    role: str = Field(default="ephemeral_executor", min_length=1)
    policy: MountPolicy = Field(default_factory=MountPolicy)
    ttl_seconds: int = Field(default=300, ge=1)
    execution_id: str | None = None
    executor_spiffe_id: str | None = None
    substrate_id: str | None = None
    boot_instance_id: str | None = None
    runtime_key_thumbprint: str | None = None
    state_root: str | None = None


class MountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    anchoring: dict[str, Any]
    mount: Mount | None = None
    token: EphemeralScopedToken | None = None
    ttl_seconds: int | None = None
    expires_at: datetime | None = None
    nonce_consumed: bool | None = None
    holder_credential: str | None = None


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource: str | None = None
    approval_token: str | None = None
    suppression_evidence: str | None = None
    # Compatibility only. Caller booleans never authorize suppression-gated actions.
    suppression_confirmed: bool = False


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    anchoring: dict[str, Any]
    mount_id: str
    action: str
    resource: str | None = None


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    operation_id: str | None = None
    approval_token: str | None = None
    suppression_evidence: str | None = None
    suppression_confirmed: bool = False
    substrate_id: str | None = None
    boot_instance_id: str | None = None
    runtime_key_thumbprint: str | None = None
    state_root: str | None = None
    authority_epoch: int | None = Field(default=None, ge=0)


class ExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    anchoring: dict[str, Any]
    mount_id: str
    action: str
    resource: str
    operation_id: str
    consequence: dict[str, Any]
    authority: dict[str, Any]


class TargetStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_ref: str
    resource: str
    workspace: str
    project: str
    mount_id: str
    state: Any


class TerminateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: UnmountReason = UnmountReason.EXPLICIT_TERMINATE


class TerminateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    anchoring: dict[str, Any]
    mount_id: str
    # Operations already authorized or started on the mount that had not
    # settled when terminate answered; an effect for these may still land.
    in_flight_operation_ids: list[str] = []


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=256)
    permit: str = Field(min_length=1, max_length=256)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sink_ref: str | None = Field(default=None, max_length=128)


class RedeemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    operation_id: str


def get_registry(request: Request, db: Session = Depends(get_session)) -> MountRegistry:
    return _build_registry(request, db)


def get_public_registry(request: Request, db: Session = Depends(get_unscoped_session)) -> MountRegistry:
    """Registry for the read-only catalog route, which needs no tenant context."""
    return _build_registry(request, db)


def _build_registry(request: Request, db: Session) -> MountRegistry:
    shared: MountRegistry = request.app.state.mount_registry
    settings = request.app.state.settings
    anchor = shared.anchor
    if isinstance(anchor, UnconfirmedAnchor):
        if settings.cappo_require_persistent_pgl:
            anchor = AuditPGLAnchor(db, settings)
        else:
            anchor = LocalConfirmedAnchor()
    verifier = BoundMountEvidenceVerifier(
        approval_key=settings.approval_token_signing_key,
        suppression_key=os.getenv("SUPPRESSION_EVIDENCE_SIGNING_KEY", ""),
    )
    registry = MountRegistry(
        db=db,
        anchor=anchor,
        evidence_verifier=verifier,
        settings=settings,
    )
    registry.packages.update(shared.packages)
    registry.target_adapters = shared.target_adapters
    return registry


def anchor_payload(status: Any, settings: Any | None = None) -> dict[str, Any]:
    # Never expose exception/debug detail from the evidence boundary.
    return {
        "status": status.status,
        "anchor_id": status.anchor_id,
        "pgl_event_hash": status.external_ref,
        "pgl_agent_id": (
            getattr(settings, "pgl_ledger_agent_id", None)
            if status.status == "confirmed"
            else None
        ),
    }


def _caller(request: Request, *, requested_workspace: str | None = None) -> tuple[str, str | None]:
    principal = request.scope.get("auth_principal")
    if not isinstance(principal, str) or not principal:
        raise HTTPException(status_code=401, detail="AUTHENTICATION_REQUIRED")

    settings = request.app.state.settings
    workspace = request.scope.get("auth_workspace")
    if not isinstance(workspace, str) or not workspace:
        workspace = None

    if settings.auth_enabled:
        if workspace is None:
            raise HTTPException(status_code=403, detail="WORKSPACE_IDENTITY_REQUIRED")
        if requested_workspace is not None and workspace != requested_workspace:
            raise HTTPException(status_code=403, detail="WORKSPACE_SCOPE_MISMATCH")
    elif requested_workspace is not None:
        workspace = requested_workspace

    return principal, workspace


def _deny_holder(request: Request) -> None:
    if request.scope.get("mount_holder_id"):
        raise HTTPException(status_code=403, detail="HOLDER_SCOPE_FORBIDDEN")


# --- Commercial metering (credits are accounting; CAPPO stays the authority) ---


class _Metered:
    """One metered call: commercial preflight before authority, settle after."""

    def __init__(self, meter: EntitlementMeter, *, workspace: str, action_type: str,
                 key: str, execution_id: str | None) -> None:
        self.meter = meter
        self.workspace = workspace
        self.action_type = action_type
        self.key = key
        self.execution_id = execution_id
        self.outcome: MeterOutcome | None = None
        self.started = perf_counter()

    def preflight(self, *, mount_id: str, principal: str, operation_ref: str | None) -> None:
        self.outcome = self.meter.charge(
            workspace_id=self.workspace, action_type=self.action_type, idempotency_key=self.key,
            mount_id=mount_id, execution_ref=self.execution_id, operation_ref=operation_ref,
            principal=principal,
        )
        if not self.outcome.allowed:
            raise HTTPException(status_code=self.outcome.status_code or 402, detail=self.outcome.denial)
        self.started = perf_counter()

    def abort(self, reason: str) -> None:
        if self.outcome and self.outcome.charged and not self.outcome.replay:
            self.meter.reverse(self.key, reason)

    def settle(self, db: Session, *, decision: Decision, reason: str,
               receipt_id: str | None = None, operation_id: str | None = None) -> None:
        o = self.outcome
        if o is None:
            return
        reversed_ = False
        if decision is Decision.DENY and o.charged and not o.replay:
            reversed_ = self.meter.reverse(self.key, reason)
        try:
            # Credit cost has no field on the signed receipt; it is recorded as
            # hash-chained audit evidence bound to the execution (run_id).
            AuditService(db).record(
                "credits_metered",
                {
                    "action_type": self.action_type,
                    "credits": o.credits,
                    "charged": o.charged and not reversed_,
                    "replay": o.replay,
                    "reversed": reversed_,
                    "unmetered_reason": o.unmetered_reason,
                    "ledger_entry_id": o.entry_id,
                    "idempotency_key": self.key,
                    "decision": decision.value,
                    "reason": reason,
                    "receipt_id": receipt_id,
                    "operation_id": operation_id,
                    # Per-execution timing for unit-economics (cost/credit) telemetry.
                    "duration_ms": round((perf_counter() - self.started) * 1000, 3),
                },
                workspace_id=self.workspace,
                run_id=self.execution_id,
                forward_to_gnomledger=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("credits_metered evidence not recorded for %s", self.key, exc_info=True)


def _metered(request: Request, registry: MountRegistry, mount_id: str, principal: str,
             workspace: str | None, *, action_type: str, key_prefix: str,
             require_mounted: bool = True) -> _Metered | None:
    meter = get_meter(request.app.state)
    if not meter.enabled:
        return None
    record, state = registry.status(mount_id, owner_principal=principal, owner_workspace=workspace)
    # Unknown/foreign/expired mounts are denied by CAPPO itself and never charged.
    if record is None or (require_mounted and state != "mounted"):
        return None
    suffix = record.token.token_id if key_prefix != "read" else str(uuid4())
    return _Metered(meter, workspace=record.mount.scope.workspace, action_type=action_type,
                    key=f"cappo:{key_prefix}:{mount_id}:{suffix}",
                    execution_id=record.token.execution_id)


def _emit_decision(request: Request, workspace: str | None, decision: Decision, *,
                   ref: str, executed: bool = False) -> None:
    meter = get_meter(request.app.state)
    meter.emit("first_authority_decision", workspace, ref=ref)
    if decision is Decision.DENY:
        meter.emit("first_denied_action", workspace, ref=ref)
    elif executed:
        meter.emit("first_governed_execution", workspace, ref=ref)


@router.get("/packages", response_model=list[CapabilityPackage])
def list_packages(
    request: Request,
    registry: MountRegistry = Depends(get_public_registry),
) -> list[CapabilityPackage]:
    _deny_holder(request)
    return registry.list_packages()


@router.get("/targets/{target_ref}/state", response_model=TargetStateResponse)
def read_target_state(
    target_ref: str,
    request: Request,
    resource: str = Query(..., min_length=1),
    mount_id: str = Query(..., min_length=1),
    registry: MountRegistry = Depends(get_registry),
    db: Session = Depends(get_session),
) -> TargetStateResponse:
    principal, workspace = _caller(request)
    if workspace is None:
        raise HTTPException(status_code=403, detail="WORKSPACE_IDENTITY_REQUIRED")

    metering = _metered(request, registry, mount_id, principal, workspace,
                        action_type=VERIFICATION_READ, key_prefix="read", require_mounted=False)
    if metering is not None:
        metering.preflight(mount_id=mount_id, principal=principal, operation_ref=None)  # fail-safe

    record, state = registry.status(
        mount_id,
        owner_principal=principal,
        owner_workspace=workspace,
    )
    if record is None:
        raise HTTPException(
            status_code=404 if state == "unknown_mount" else 403,
            detail=state,
        )

    # Read the same workspace/project tuple that the persisted mount used for
    # execution. In auth-disabled canary mode the middleware supplies the
    # deployment default workspace on later requests, while mount creation is
    # explicitly scoped by the request. Using the caller value here can report
    # a different counter than the one CAPPO actually mutated.
    workspace = record.mount.scope.workspace
    project = record.mount.scope.project

    adapter = registry.target_adapters.resolve(target_ref)
    if adapter is None:
        raise HTTPException(status_code=404, detail="unknown_target")

    try:
        validate_resource(resource)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_target_resource") from exc

    read_action = next(
        (candidate for candidate in sorted(adapter.actions) if candidate.endswith(".read")),
        None,
    )
    if read_action is None and getattr(adapter, "state_readable", False):
        # A write-only target that still publishes its own state summary (HTTP targets).
        read_action = "target.state"
    if read_action is None:
        raise HTTPException(status_code=400, detail="target_not_readable")

    context = ConsequenceContext(
        action=read_action,
        resource=resource,
        arguments={},
        operation_id=f"read_{uuid4()}",
        workspace=workspace,
        project=project,
    )
    try:
        state = adapter.read_state(context)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="resource_not_found") from exc
    except ValueError as exc:
        if str(exc) == "invalid_target_resource":
            raise HTTPException(status_code=400, detail="invalid_target_resource") from exc
        raise

    if metering is not None:
        metering.settle(db, decision=Decision.ALLOW, reason="verification_read")

    return TargetStateResponse(
        target_ref=target_ref,
        resource=resource,
        workspace=workspace,
        project=project,
        mount_id=mount_id,
        state=state,
    )


@router.post("/mounts", response_model=MountResponse)
def request_mount(
    body: MountRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> MountResponse:
    _deny_holder(request)
    principal, workspace = _caller(request, requested_workspace=body.execution_scope.workspace)
    assert workspace is not None
    scope = body.execution_scope.model_copy(
        update={
            "reads": body.requested_action_scope.reads,
            "writes": body.requested_action_scope.writes,
            "blocked": body.requested_action_scope.blocked,
        }
    )
    record, anchor, reason, holder_credential = registry.request_mount(
        body.package_ref,
        scope,
        role=body.role,
        policy=body.policy,
        ttl_seconds=min(body.ttl_seconds, registry.mounter.MAX_TTL_SECONDS),
        owner_principal=principal,
        owner_workspace=workspace,
        execution_id=body.execution_id,
        caller_spiffe_id=request.scope.get("caller_spiffe_id"),
        executor_spiffe_id=request.scope.get("executor_spiffe_id") or request.scope.get("caller_spiffe_id"),
        substrate_id=body.substrate_id,
        boot_instance_id=body.boot_instance_id,
        runtime_key_thumbprint=body.runtime_key_thumbprint,
        state_root=body.state_root,
    )
    if record is None:
        return MountResponse(
            decision=Decision.DENY,
            reason=reason,
            anchoring=anchor_payload(anchor, request.app.state.settings),
            holder_credential=None,
        )
    get_meter(request.app.state).emit("capability_issued", workspace, ref=record.mount.id,
                                      details={"package_ref": record.mount.package_ref})
    return MountResponse(
        decision=Decision.ALLOW,
        reason=reason,
        anchoring=anchor_payload(anchor, request.app.state.settings),
        mount=record.mount,
        token=record.token,
        ttl_seconds=record.token.ttl_seconds,
        expires_at=record.token.expires_at,
        nonce_consumed=record.token.nonce_consumed,
        holder_credential=holder_credential,
    )


@router.get("/mounts/{mount_id}", response_model=MountResponse)
def mount_status(
    mount_id: str,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> MountResponse:
    principal, workspace = _caller(request)
    record, state = registry.status(
        mount_id,
        owner_principal=principal,
        owner_workspace=workspace,
    )
    if record is None:
        return MountResponse(
            decision=Decision.DENY,
            reason=state,
            anchoring=anchor_payload(
                AnchorResult("not_applicable"),
                request.app.state.settings,
            ),
        )
    mount = record.mount
    if state != "mounted":
        mount = mount.model_copy(
            update={
                "lifecycle": mount.lifecycle.model_copy(update={"state": LifecycleState(state)})
            }
        )
    return MountResponse(
        decision=Decision.ALLOW if state == "mounted" else Decision.DENY,
        reason=state,
        anchoring=anchor_payload(
            record.anchoring or AnchorResult("not_applicable"),
            request.app.state.settings,
        ),
        mount=mount,
        # Status never re-discloses token_id or nonce.
        ttl_seconds=record.token.ttl_seconds,
        expires_at=record.token.expires_at,
        nonce_consumed=record.token.nonce_consumed,
    )


@router.post("/mounts/{mount_id}/actions", response_model=ActionResponse)
def evaluate_action(
    mount_id: str,
    body: ActionRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
    db: Session = Depends(get_session),
) -> ActionResponse:
    principal, workspace = _caller(request)
    metering = _metered(request, registry, mount_id, principal, workspace,
                        action_type=GOVERNED_ACTION, key_prefix="action")
    if metering is not None:
        metering.key = f"{metering.key}:{body.action}"
        metering.preflight(mount_id=mount_id, principal=principal, operation_ref=None)
    spiffe_fields = {
        "caller_spiffe_id": request.scope.get("caller_spiffe_id"),
        "trust_domain": request.scope.get("trust_domain"),
        "caller_cert_sha256": request.scope.get("caller_cert_sha256"),
        "svid_not_before": request.scope.get("svid_not_before"),
        "svid_not_after": request.scope.get("svid_not_after"),
        "eei_id": request.headers.get("x-veklom-eei-id"),
        "profile_id": request.headers.get("x-veklom-profile-id"),
        "lease_id": request.headers.get("x-veklom-lease-id"),
        "operator_id": request.headers.get("x-veklom-operator-id"),
    }

    try:
        decision, reason, anchor, detail = registry.evaluate(
            mount_id,
            body.action,
            resource=body.resource,
            token_id=body.token_id,
            nonce=body.nonce,
            owner_principal=principal,
            owner_workspace=workspace,
            approval_token=body.approval_token,
            suppression_evidence=body.suppression_evidence,
            suppression_confirmed=body.suppression_confirmed,
            spiffe_fields=spiffe_fields,
        )
    except Exception:
        if metering is not None:
            metering.abort("cappo_error")
        raise
    if metering is not None:
        metering.settle(db, decision=decision, reason=reason,
                        receipt_id=(detail or {}).get("receipt_id") if isinstance(detail, dict) else None)
        _emit_decision(request, metering.workspace, decision, ref=mount_id)
    return ActionResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor, request.app.state.settings),
        mount_id=mount_id,
        action=body.action,
        resource=body.resource,
    )


class StartClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=256)


class StartClaimResponse(BaseModel):
    decision: Decision
    reason: str
    mount_id: str
    operation_id: str


@router.post("/mounts/{mount_id}/start-claim", response_model=StartClaimResponse)
def claim_start(
    mount_id: str,
    body: StartClaimRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> StartClaimResponse:
    """Atomic, CAPPO-recorded right to start work whose consequence comes later.

    Consumes nothing; one claim per mount; refused once the mount is terminated, expired or used.
    """
    principal, workspace = _caller(request)
    decision, reason = registry.claim_start(
        mount_id,
        token_id=body.token_id,
        nonce=body.nonce,
        operation_id=body.operation_id,
        owner_principal=principal,
        owner_workspace=workspace,
    )
    return StartClaimResponse(decision=decision, reason=reason, mount_id=mount_id, operation_id=body.operation_id)


@router.post("/mounts/{mount_id}/execute", response_model=ExecuteResponse)
def execute_consequence(
    mount_id: str,
    body: ExecuteRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
    db: Session = Depends(get_session),
) -> ExecuteResponse:
    principal, workspace = _caller(request)
    metering = _metered(request, registry, mount_id, principal, workspace,
                        action_type=GOVERNED_EXECUTION, key_prefix="execute")
    if metering is not None:
        metering.preflight(mount_id=mount_id, principal=principal, operation_ref=body.operation_id)
    spiffe_fields = {
        "caller_spiffe_id": request.scope.get("caller_spiffe_id"),
        "trust_domain": request.scope.get("trust_domain"),
        "caller_cert_sha256": request.scope.get("caller_cert_sha256"),
        "svid_not_before": request.scope.get("svid_not_before"),
        "svid_not_after": request.scope.get("svid_not_after"),
        "eei_id": request.headers.get("x-veklom-eei-id"),
        "profile_id": request.headers.get("x-veklom-profile-id"),
        "lease_id": request.headers.get("x-veklom-lease-id"),
        "operator_id": request.headers.get("x-veklom-operator-id"),
    }
    try:
        decision, reason, consequence_state, payload = registry.execute_consequence(
            mount_id,
            body.action,
            token_id=body.token_id,
            nonce=body.nonce,
            owner_principal=principal,
            owner_workspace=workspace,
            approval_token=body.approval_token,
            suppression_evidence=body.suppression_evidence,
            suppression_confirmed=body.suppression_confirmed,
            target_ref=body.target_ref,
            resource=body.resource,
            arguments=body.arguments,
            operation_id=body.operation_id,
            spiffe_fields=spiffe_fields,
            substrate_id=body.substrate_id,
            boot_instance_id=body.boot_instance_id,
            runtime_key_thumbprint=body.runtime_key_thumbprint,
            state_root=body.state_root,
            authority_epoch=body.authority_epoch,
        )
    except Exception:
        if metering is not None:
            metering.abort("cappo_error")
        raise
    if metering is not None:
        metering.settle(db, decision=decision, reason=reason, receipt_id=payload.get("receipt_id"),
                        operation_id=payload.get("operation_id"))
        _emit_decision(request, metering.workspace, decision, ref=mount_id,
                       executed=bool(payload.get("target_invoked")))
    return ExecuteResponse(
        decision=decision,
        reason=reason,
        anchoring=payload["anchoring"],
        mount_id=mount_id,
        action=body.action,
        resource=body.resource,
        operation_id=payload["operation_id"],
        consequence={
            "state": consequence_state,
            "target_invoked": payload["target_invoked"],
            "target_ref": body.target_ref,
            "resource": body.resource,
            "resulting_state": payload["resulting_state"],
            "receipt_id": payload["receipt_id"],
            "terminated": payload["terminated"],
        },
        authority={
            "execution_id": payload["execution_id"],
            "nonce_consumed": payload["nonce_consumed"],
        },
    )


@router.post("/mounts/{mount_id}/terminate", response_model=TerminateResponse)
def terminate_mount(
    mount_id: str,
    body: TerminateRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> TerminateResponse:
    principal, workspace = _caller(request)
    decision, reason, anchor, in_flight = registry.terminate_with_in_flight(
        mount_id,
        body.reason,
        owner_principal=principal,
        owner_workspace=workspace,
    )
    return TerminateResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor, request.app.state.settings),
        mount_id=mount_id,
        in_flight_operation_ids=in_flight,
    )


@router.post("/redeem", response_model=RedeemResponse)
def redeem_consequence(
    body: RedeemRequest,
    request: Request,
    db: Session = Depends(get_unscoped_session),
) -> RedeemResponse:
    """Target-side authority check: called by a target immediately before it commits.

    The caller is a target, not a user, so there is no session. The target identifies
    itself (sink_ref) and proves it with an Ed25519 signature over this redemption in
    X-Veklom-Target-Signature, verified against the key pinned for it in CAPPO's
    target configuration. The permit is bound to the operation, the exact payload and
    that target, and it only ever answers whether CAPPO's own dispatched,
    still-authorized consequence may commit now, at this target.
    """
    registry = _build_registry(request, db)
    decision, reason = registry.redeem_consequence(
        body.operation_id,
        body.permit,
        body.payload_sha256,
        sink_ref=body.sink_ref,
        target_signature=request.headers.get("x-veklom-target-signature"),
    )
    return RedeemResponse(decision=decision, reason=reason, operation_id=body.operation_id)
