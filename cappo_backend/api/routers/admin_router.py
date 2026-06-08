"""Administrative endpoints: kill switch, workspace budget, EI revocation.

These are operational governance controls (migration note §7 kill-switch/budget;
EI Plan §Rollout Phase 4 revocation). They are intentionally separate from the
governed ``/v1/exec`` path. Authn/authz for these admin routes is out of scope
for this phase and would be layered ahead of them (see main.py middleware notes).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
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
