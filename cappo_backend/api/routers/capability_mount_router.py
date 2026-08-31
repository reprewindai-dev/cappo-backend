"""Capability package discovery and ephemeral mount lifecycle endpoints."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

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
    MountRegistry,
    UnconfirmedAnchor,
)
from cappo_backend.db.session import get_session
from cappo_backend.services.mount_evidence import BoundMountEvidenceVerifier
from cappo_backend.services.mount_pgl import AuditPGLAnchor

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


class TerminateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: UnmountReason = UnmountReason.EXPLICIT_TERMINATE


class TerminateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    anchoring: dict[str, Any]
    mount_id: str


def get_registry(request: Request, db: Session = Depends(get_session)) -> MountRegistry:
    shared: MountRegistry = request.app.state.mount_registry
    settings = request.app.state.settings
    anchor = shared.anchor
    if isinstance(anchor, UnconfirmedAnchor):
        anchor = AuditPGLAnchor(db, settings)
    verifier = BoundMountEvidenceVerifier(
        approval_key=settings.approval_token_signing_key,
        suppression_key=os.getenv("SUPPRESSION_EVIDENCE_SIGNING_KEY", ""),
    )
    registry = MountRegistry(db=db, anchor=anchor, evidence_verifier=verifier)
    registry.packages.update(shared.packages)
    registry.effect_targets = shared.effect_targets
    return registry


def anchor_payload(status: Any) -> dict[str, Any]:
    # Never expose exception/debug detail from the evidence boundary.
    return {"status": status.status, "anchor_id": status.anchor_id}


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


@router.get("/packages", response_model=list[CapabilityPackage])
def list_packages(registry: MountRegistry = Depends(get_registry)) -> list[CapabilityPackage]:
    return registry.list_packages()


@router.post("/mounts", response_model=MountResponse)
def request_mount(
    body: MountRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> MountResponse:
    principal, workspace = _caller(request, requested_workspace=body.execution_scope.workspace)
    assert workspace is not None
    scope = body.execution_scope.model_copy(
        update={
            "reads": body.requested_action_scope.reads,
            "writes": body.requested_action_scope.writes,
            "blocked": body.requested_action_scope.blocked,
        }
    )
    record, anchor, reason = registry.request_mount(
        body.package_ref,
        scope,
        role=body.role,
        policy=body.policy,
        ttl_seconds=min(body.ttl_seconds, registry.mounter.MAX_TTL_SECONDS),
        owner_principal=principal,
        owner_workspace=workspace,
        execution_id=body.execution_id,
        caller_spiffe_id=request.scope.get("caller_spiffe_id"),
        executor_spiffe_id=body.executor_spiffe_id or request.scope.get("caller_spiffe_id"),
    )
    if record is None:
        return MountResponse(
            decision=Decision.DENY,
            reason=reason,
            anchoring=anchor_payload(anchor),
        )
    return MountResponse(
        decision=Decision.ALLOW,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount=record.mount,
        token=record.token,
        ttl_seconds=record.token.ttl_seconds,
        expires_at=record.token.expires_at,
        nonce_consumed=record.token.nonce_consumed,
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
            anchoring={"status": "not_applicable", "anchor_id": None},
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
        anchoring=anchor_payload(record.anchoring or AnchorResult("not_applicable")),
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
) -> ActionResponse:
    principal, workspace = _caller(request)
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

    decision, reason, anchor, _ = registry.evaluate(
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
    return ActionResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount_id=mount_id,
        action=body.action,
        resource=body.resource,
    )


@router.post("/mounts/{mount_id}/execute", response_model=ExecuteResponse)
def execute_consequence(
    mount_id: str,
    body: ExecuteRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> ExecuteResponse:
    principal, workspace = _caller(request)
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
    )
    return ExecuteResponse(
        decision=decision,
        reason=reason,
        anchoring={"status": "not_applicable", "anchor_id": None},
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
    decision, reason, anchor = registry.terminate(
        mount_id,
        body.reason,
        owner_principal=principal,
        owner_workspace=workspace,
    )
    return TerminateResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount_id=mount_id,
    )
