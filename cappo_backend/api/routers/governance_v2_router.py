"""MCPAPI v2.0 governance endpoints (Safety / Intelligence / Governance).

Exposes the v2 layers so the control plane can surface them to operators:

- ``GET  /v1/governance/v2/risk/{agent_id}``        — current risk profile
- ``POST /v1/governance/v2/assess``                 — full pre-execution assessment
- ``GET  /v1/governance/v2/quarantine``             — quarantine queue
- ``POST /v1/governance/v2/quarantine/{id}/approve``— record an approval
- ``POST /v1/governance/v2/quarantine/{id}/deny``   — deny a quarantined request

These read/assess endpoints never touch the database; they operate on the
in-memory v2 stack. Authn is handled by the app's auth middleware.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from cappo_backend.services.mcp_v2 import CurrentMetric, get_mcp_v2_stack
from cappo_backend.services.safety import ApproverTrustError, SelfApprovalForbiddenError

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
    approver_id: str
    approver_trust: float


class DenyRequest(BaseModel):
    reason: str


@router.post("/assess")
def assess(body: AssessRequest, request: Request) -> dict[str, Any]:
    stack = get_mcp_v2_stack()
    metric = CurrentMetric(
        requests_per_hour=body.requests_per_hour,
        failure_rate=body.failure_rate,
        time_of_day=body.time_of_day,
        new_capabilities=tuple(body.new_capabilities),
    )
    jwt_payload = request.scope.get("jwt_payload") if isinstance(request.scope.get("jwt_payload"), dict) else {}
    auth_agent = (
        getattr(request.state, "agent_id", None)
        or (getattr(request.state, "verified_eat", {}).get("subject") if hasattr(request.state, "verified_eat") and isinstance(request.state.verified_eat, dict) else None)
        or jwt_payload.get("sub")
        or jwt_payload.get("agent_id")
        or request.headers.get("X-Authenticated-Agent-Id")
        or body.agent_id
    )
    return stack.pre_execution_assessment(
        auth_agent,
        body.request,
        metric=metric,
        trust_score=body.trust_score,
        capability_id=body.capability_id,
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
def quarantine_queue() -> dict[str, Any]:
    stack = get_mcp_v2_stack()
    return {
        "items": [
            {
                "quarantine_id": qr.quarantine_id,
                "reason": qr.quarantine_reason,
                "status": qr.status,
                "approval_required": qr.approval_required,
                "approvers_required": qr.approvers_required,
                "approvals_received": list(qr.approvals_received),
                "deadline": qr.approval_deadline.isoformat(),
                "requester_id": qr.requester_id,
            }
            for qr in stack.quarantine.all()
        ]
    }


@router.post("/quarantine/{quarantine_id}/approve")
def approve_quarantine(quarantine_id: str, body: ApproveRequest, request: Request) -> dict[str, Any]:
    stack = get_mcp_v2_stack()
    if stack.quarantine.get(quarantine_id) is None:
        raise HTTPException(status_code=404, detail="quarantine not found")

    # Authoritative identity extraction from verified auth context / headers / JWT payload
    jwt_payload = request.scope.get("jwt_payload") if isinstance(request.scope.get("jwt_payload"), dict) else {}
    auth_principal = request.scope.get("auth_principal")
    jwt_sub = jwt_payload.get("sub") or jwt_payload.get("agent_id") or jwt_payload.get("subject")

    auth_principal_name = None
    if auth_principal and not auth_principal.startswith("api-key:"):
        if auth_principal.startswith("spiffe://"):
            auth_principal_name = auth_principal
        elif auth_principal.startswith("jwt:") or auth_principal.startswith("mtls:"):
            auth_principal_name = auth_principal.split(":")[-1]
            if auth_principal_name.startswith("CN="):
                auth_principal_name = auth_principal_name.removeprefix("CN=")
        else:
            auth_principal_name = auth_principal

    auth_approver = (
        getattr(request.state, "agent_id", None)
        or (getattr(request.state, "verified_eat", {}).get("subject") if hasattr(request.state, "verified_eat") and isinstance(request.state.verified_eat, dict) else None)
        or jwt_sub
        or request.headers.get("X-Authenticated-Agent-Id")
        or auth_principal_name
    )
    try:
        reached = stack.quarantine.approve(
            quarantine_id,
            body.approver_id,
            approver_trust=body.approver_trust,
            authenticated_approver_id=auth_approver,
        )
    except (ApproverTrustError, SelfApprovalForbiddenError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"quorum_reached": reached, "status": stack.quarantine.get(quarantine_id).status}


@router.post("/quarantine/{quarantine_id}/deny")
def deny_quarantine(quarantine_id: str, body: DenyRequest) -> dict[str, Any]:
    stack = get_mcp_v2_stack()
    if stack.quarantine.get(quarantine_id) is None:
        raise HTTPException(status_code=404, detail="quarantine not found")
    stack.quarantine.deny(quarantine_id, body.reason)
    return {"status": stack.quarantine.get(quarantine_id).status}
