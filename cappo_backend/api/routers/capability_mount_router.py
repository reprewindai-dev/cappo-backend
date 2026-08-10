"""Capability package discovery and ephemeral mount lifecycle endpoints."""

from __future__ import annotations

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


class MountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    anchoring: dict[str, Any]
    mount: Mount | None = None
    token: EphemeralScopedToken | None = None
    nonce_consumed: bool | None = None


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    action: str = Field(min_length=1)
    approval_token: str | None = None
    suppression_confirmed: bool = False


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    anchoring: dict[str, Any]
    mount_id: str
    action: str


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
    anchor = shared.anchor
    if isinstance(anchor, UnconfirmedAnchor):
        anchor = AuditPGLAnchor(db, request.app.state.settings)
    registry = MountRegistry(db=db, anchor=anchor)
    registry.packages.update(shared.packages)
    return registry


def anchor_payload(status: Any) -> dict[str, Any]:
    # Never expose exception/debug detail from the evidence boundary.
    return {
        "status": status.status,
        "anchor_id": status.anchor_id,
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
    if state != "mounted":
        return MountResponse(
            decision=Decision.DENY,
            reason=state,
            anchoring=anchor_payload(record.anchoring or AnchorResult("not_applicable")),
            mount=record.mount.model_copy(
                update={
                    "lifecycle": record.mount.lifecycle.model_copy(
                        update={"state": LifecycleState(state)}
                    )
                }
            ),
            nonce_consumed=record.token.nonce_consumed,
        )
    return MountResponse(
        decision=Decision.ALLOW,
        reason=state,
        anchoring=anchor_payload(record.anchoring or AnchorResult("not_applicable")),
        mount=record.mount,
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
    decision, reason, anchor, _ = registry.evaluate(
        mount_id,
        body.action,
        token_id=body.token_id,
        nonce=body.nonce,
        owner_principal=principal,
        owner_workspace=workspace,
        approval_token=body.approval_token,
        suppression_confirmed=body.suppression_confirmed,
    )
    return ActionResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount_id=mount_id,
        action=body.action,
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
