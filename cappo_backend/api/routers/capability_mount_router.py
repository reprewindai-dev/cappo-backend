"""Capability package discovery and ephemeral mount lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    EphemeralScopedToken,
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
    registry._records = shared._records  # noqa: SLF001
    return registry


def anchor_payload(status: Any) -> dict[str, Any]:
    return {
        "status": status.status,
        "anchor_id": status.anchor_id,
        "detail": status.detail,
    }


@router.get("/packages", response_model=list[CapabilityPackage])
def list_packages(registry: MountRegistry = Depends(get_registry)) -> list[CapabilityPackage]:
    return registry.list_packages()


@router.post("/mounts", response_model=MountResponse)
def request_mount(
    body: MountRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> MountResponse:
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
        execution_id=body.execution_id,
    )
    if record is None:
        return MountResponse(
            decision=Decision.DENY,
            reason=reason,
            anchoring=anchor_payload(anchor),
        )
    request.app.state.mount_registry._records[record.mount.id] = record  # noqa: SLF001
    return MountResponse(
        decision=Decision.ALLOW,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount=record.mount,
        token=record.token,
    )


@router.get("/mounts/{mount_id}", response_model=MountResponse)
def mount_status(
    mount_id: str,
    registry: MountRegistry = Depends(get_registry),
) -> MountResponse:
    record = registry.get(mount_id)
    if record is None:
        return MountResponse(
            decision=Decision.DENY,
            reason="unknown_mount",
            anchoring={"status": "not_applicable"},
        )
    return MountResponse(
        decision=Decision.ALLOW,
        reason="mounted",
        anchoring=anchor_payload(record.anchoring or AnchorResult("not_applicable")),
        mount=record.mount,
        token=record.token,
    )


@router.post("/mounts/{mount_id}/actions", response_model=ActionResponse)
def evaluate_action(
    mount_id: str,
    body: ActionRequest,
    registry: MountRegistry = Depends(get_registry),
) -> ActionResponse:
    decision, reason, anchor, _ = registry.evaluate(
        mount_id,
        body.action,
        token_id=body.token_id,
        nonce=body.nonce,
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
    registry: MountRegistry = Depends(get_registry),
) -> TerminateResponse:
    decision, reason, anchor = registry.terminate(mount_id, body.reason)
    return TerminateResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount_id=mount_id,
    )
