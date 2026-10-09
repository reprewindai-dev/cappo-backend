"""MCPAPI v2.0 governance endpoints (Safety / Intelligence / Governance).

Exposes the v2 layers so the control plane can surface them to operators:

- ``GET  /v1/governance/v2/risk/{agent_id}``        — current risk profile
- ``POST /v1/governance/v2/assess``                 — full pre-execution assessment
- ``GET  /v1/governance/v2/quarantine``             — quarantine queue (caller's workspace)
- ``POST /v1/governance/v2/quarantine/{id}/approve``— record an approval
- ``POST /v1/governance/v2/quarantine/{id}/deny``   — deny a quarantined request

These read/assess endpoints never touch the database; they operate on the
in-memory v2 stack. Authn is handled by the app's auth middleware.

Authorization model for the quarantine controls (see
:mod:`cappo_backend.services.governance_approvers`):

- The approver identity, workspace and trust come ONLY from the verified auth
  context the middleware placed in the ASGI scope. Nothing in the request body
  or in client headers can name the approver or raise their trust.
- Every quarantine surface is scoped to the caller's workspace. An item bound
  to another workspace is indistinguishable from a missing one (404).
- ``approve`` requires a registered approver whose server-held trust meets
  ``APPROVER_MIN_TRUST``. ``deny`` requires the same authority, or the session
  credential of the owner of the workspace the request belongs to.
- Self-approval (approver identity bound to the quarantined request) is always
  refused, whatever the approver's trust.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from cappo_backend.services.governance_approvers import (
    ApproverContext,
    ApproverResolutionError,
    resolve_approver,
)
from cappo_backend.services.mcp_v2 import CurrentMetric, get_mcp_v2_stack
from cappo_backend.services.safety import (
    ApproverTrustError,
    QuarantinedRequest,
    SelfApprovalForbiddenError,
    normalize_identity,
)

router = APIRouter(prefix="/v1/governance/v2")


class AssessRequest(BaseModel):
    agent_id: str
    trust_score: float = 75.0
    capability_id: str = "exec"
    requests_per_hour: float = 0.0
    failure_rate: float = 0.0
    time_of_day: int = Field(default=12, ge=0, le=23)
    new_capabilities: list[str] = Field(default_factory=list)
    request: dict[str, Any] = Field(default_factory=dict)


class ApproveRequest(BaseModel):
    """Optional body for ``approve``.

    The approver is the authenticated caller. ``approver_id`` may be sent only
    as a confirmation of who the client believes it is; a value that differs
    from the verified identity is refused. Any other field (notably a
    self-declared ``approver_trust``) is rejected by schema validation.
    """

    model_config = ConfigDict(extra="forbid")

    approver_id: str | None = None


class DenyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _settings(request: Request) -> Any:
    return getattr(request.app.state, "settings", None)


def _verified_subject(request: Request) -> str | None:
    """Subject of a verified session credential, or ``None``. Never reads client headers."""
    payload = request.scope.get("jwt_payload")
    if isinstance(payload, dict):
        subject = payload.get("sub") or payload.get("agent_id")
        if subject and str(subject).strip():
            return str(subject).strip()
    return None


def _caller_workspace(request: Request) -> str:
    workspace = request.scope.get("auth_workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        raise HTTPException(
            status_code=403,
            detail=(
                "WORKSPACE_CONTEXT_MISSING: the credential must resolve to a workspace "
                "before the quarantine queue can be served."
            ),
        )
    return workspace.strip()


def _approver(request: Request) -> ApproverContext:
    try:
        return resolve_approver(request, _settings(request))
    except ApproverResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}") from exc


def _scoped_item(request: Request, quarantine_id: str) -> tuple[QuarantinedRequest, str]:
    """Return the item and the caller's workspace, or 404 if it is not in that workspace."""
    workspace = _caller_workspace(request)
    stack = get_mcp_v2_stack()
    qr = stack.quarantine.get_in_workspace(quarantine_id, workspace)
    if qr is None:
        raise HTTPException(status_code=404, detail="quarantine not found")
    return qr, workspace


def _serialize(qr: QuarantinedRequest) -> dict[str, Any]:
    return {
        "quarantine_id": qr.quarantine_id,
        "workspace_id": qr.workspace_id,
        "reason": qr.quarantine_reason,
        "status": qr.status,
        "approval_required": qr.approval_required,
        "approvers_required": qr.approvers_required,
        "approvals_received": list(qr.approvals_received),
        "deadline": qr.approval_deadline.isoformat(),
        "requester_id": qr.requester_id,
        "resolved_by": qr.resolved_by,
        "resolution_reason": qr.resolution_reason,
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.post("/assess")
def assess(body: AssessRequest, request: Request) -> dict[str, Any]:
    stack = get_mcp_v2_stack()
    metric = CurrentMetric(
        requests_per_hour=body.requests_per_hour,
        failure_rate=body.failure_rate,
        time_of_day=body.time_of_day,
        new_capabilities=tuple(body.new_capabilities),
    )
    # The assessed agent is the verified session subject when the credential
    # carries one; otherwise the body's agent_id. Client headers are never read.
    workspace = request.scope.get("auth_workspace")
    return stack.pre_execution_assessment(
        _verified_subject(request) or body.agent_id,
        body.request,
        metric=metric,
        trust_score=body.trust_score,
        capability_id=body.capability_id,
        workspace_id=workspace if isinstance(workspace, str) and workspace.strip() else None,
    )


@router.get("/risk/{agent_id}")
def risk_profile(agent_id: str) -> dict[str, Any]:
    stack = get_mcp_v2_stack()
    profile = stack.risk._profiles.get(agent_id)  # noqa: SLF001 - read-only accessor
    if profile is None:
        return {"agent_id": agent_id, "assessed": False}
    return {
        "agent_id": agent_id,
        "assessed": True,
        "overall_risk_score": profile.overall_risk_score,
        "threat_level": profile.threat_level,
        "needs_intervention": stack.risk.needs_intervention(agent_id),
        "recommended_actions": list(profile.recommended_actions),
        "risk_factors": [
            {"factor": f.factor_name, "contribution": f.contribution, "severity": f.severity}
            for f in profile.risk_factors
        ],
    }


@router.get("/quarantine")
def quarantine_queue(request: Request) -> dict[str, Any]:
    workspace = _caller_workspace(request)
    stack = get_mcp_v2_stack()
    return {
        "workspace_id": workspace,
        "items": [_serialize(qr) for qr in stack.quarantine.for_workspace(workspace)],
    }


@router.post("/quarantine/{quarantine_id}/approve")
def approve_quarantine(
    quarantine_id: str,
    request: Request,
    body: ApproveRequest | None = None,
) -> dict[str, Any]:
    approver = _approver(request)
    qr, _workspace = _scoped_item(request, quarantine_id)

    claimed = body.approver_id if body is not None else None
    if claimed is not None and normalize_identity(claimed) != normalize_identity(approver.approver_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "APPROVER_IDENTITY_MISMATCH: the approver is the authenticated caller; "
                "a body approver_id naming someone else is refused."
            ),
        )

    stack = get_mcp_v2_stack()
    try:
        reached = stack.quarantine.approve(
            qr.quarantine_id,
            approver.approver_id,
            approver_trust=approver.trust,
            authenticated_approver_id=approver.approver_id,
        )
    except SelfApprovalForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApproverTrustError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"APPROVER_NOT_AUTHORIZED: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "quorum_reached": reached,
        "status": qr.status,
        "approver_id": approver.approver_id,
        "approvals_received": list(qr.approvals_received),
    }


@router.post("/quarantine/{quarantine_id}/deny")
def deny_quarantine(quarantine_id: str, body: DenyRequest, request: Request) -> dict[str, Any]:
    approver = _approver(request)
    qr, _workspace = _scoped_item(request, quarantine_id)

    if not approver.may_deny(qr.workspace_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "DENY_NOT_AUTHORIZED: denying a quarantined request requires a registered "
                "approver or the owner of the request's workspace."
            ),
        )
    if qr.status != "quarantined":
        raise HTTPException(status_code=409, detail=f"quarantine already resolved: {qr.status}")

    stack = get_mcp_v2_stack()
    stack.quarantine.deny(qr.quarantine_id, body.reason, denied_by=approver.approver_id)
    return {"status": qr.status, "resolved_by": qr.resolved_by}
