"""Administrative endpoints: kill switch, workspace budget, EI revocation.

These are operational governance controls (migration note §7 kill-switch/budget;
EI Plan §Rollout Phase 4 revocation). They are intentionally separate from the
governed ``/v1/exec`` path. Authn/authz for these admin routes is out of scope
for this phase and would be layered ahead of them (see main.py middleware notes).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.payment_gate import PaymentGate
from cappo_backend.services.revocation_service import (
    RevocationService,
    UnknownExecutionIdentityError,
)

router = APIRouter(prefix="/v1")


# ---------- request shapes ----------

class KillSwitchRequest(BaseModel):
    active: bool
    reason: str | None = None


class BudgetRequest(BaseModel):
    balance_cents: int


class RevokeRequest(BaseModel):
    reason: str | None = None


# ---------- kill switch ----------

@router.put("/kill-switch/{workspace_id}")
def set_kill_switch(
    workspace_id: str,
    body: KillSwitchRequest,
    db: Session = Depends(get_session),
) -> dict[str, object]:
    gate = PaymentGate(db)
    switch = gate.set_kill_switch(workspace_id, active=body.active, reason=body.reason)
    db.commit()
    return {"workspace_id": switch.workspace_id, "active": switch.active, "reason": switch.reason}


# ---------- budget ----------

@router.put("/budget/{workspace_id}")
def set_budget(
    workspace_id: str,
    body: BudgetRequest,
    db: Session = Depends(get_session),
) -> dict[str, object]:
    gate = PaymentGate(db)
    budget = gate.set_budget(workspace_id, body.balance_cents)
    db.commit()
    return {"workspace_id": budget.workspace_id, "balance_cents": budget.balance_cents}


# ---------- EI revocation ----------

@router.post("/identities/{execution_id}/revoke")
def revoke_identity(
    execution_id: str,
    body: RevokeRequest,
    db: Session = Depends(get_session),
) -> dict[str, object]:
    audit = AuditService(db)
    service = RevocationService(db, audit)
    try:
        ei = service.revoke(execution_id, reason=body.reason)
    except UnknownExecutionIdentityError:
        raise HTTPException(status_code=404, detail=f"execution identity {execution_id} not found")
    db.commit()
    return {
        "execution_id": ei.execution_id,
        "revoked": ei.revoked,
        "revoked_at": ei.revoked_at.isoformat() if ei.revoked_at else None,
    }


# ---------- Audit logs and Runs feed ----------


@router.get("/audit-logs")
def get_audit_logs(
    limit: int = 50,
    workspace_id: str | None = None,
    db: Session = Depends(get_session),
):
    query = db.query(AuditEvent)
    if workspace_id:
        query = query.filter(AuditEvent.workspace_id == workspace_id)
    events = query.order_by(desc(AuditEvent.created_at)).limit(limit).all()
    return [
        {
            "log_id": e.log_id,
            "operation_type": e.operation_type,
            "workspace_id": e.workspace_id,
            "run_id": e.run_id,
            "payload": e.payload,
            "previous_log_hash": e.previous_log_hash,
            "log_hash": e.log_hash,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/runs")
def get_runs(
    limit: int = 50,
    workspace_id: str | None = None,
    db: Session = Depends(get_session),
):
    query = db.query(GovernedRun)
    if workspace_id:
        query = query.filter(GovernedRun.workspace_id == workspace_id)
    runs = query.order_by(desc(GovernedRun.created_at)).limit(limit).all()
    return [
        {
            "run_id": r.run_id,
            "workspace_id": r.workspace_id,
            "tenant_id": r.tenant_id,
            "state": r.state,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "approved_budget_cents": r.approved_budget_cents,
            "reserve_cents": r.reserve_cents,
            "delegation_depth": r.delegation_depth,
            "scope": r.scope,
            "governance_decision": r.governance_decision,
            "risk_tier": r.risk_tier,
            "pgl_identity": r.pgl_identity,
            "execution_identity": r.execution_identity,
            "eat": r.eat,
            "result_payload": r.result_payload,
        }
        for r in runs
    ]
