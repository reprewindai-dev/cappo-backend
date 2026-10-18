"""Read-only projection of governed n8n execution truth."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from cappo_backend.models.workspace_budget import WorkspaceBudgetHold
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment

router = APIRouter(prefix="/api/v1/n8n/executions", tags=["n8n-executions"])


def _project(db: Session, execution_id: str, workspace_id: str) -> dict:
    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(
            ConsequenceExecutionEvent.execution_id == execution_id,
            ConsequenceExecutionEvent.principal == workspace_id,
        )
        .order_by(ConsequenceExecutionEvent.version.asc())
    ).scalars().all()
    if not events:
        raise HTTPException(status_code=404, detail="Execution not found")

    hold = db.get(WorkspaceBudgetHold, execution_id)
    payment = db.execute(
        select(X402ConsumedPayment).where(X402ConsumedPayment.execution_id == execution_id)
    ).scalar_one_or_none()
    audit = db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.operation_type == "n8n_local_budget_settled",
            AuditEvent.run_id == execution_id,
        )
        .order_by(AuditEvent.created_at.desc())
    ).scalars().first()
    latest = events[-1]
    terminal_success = latest.state in {"succeeded", "reconciled_succeeded"}
    terminal_failure = latest.state in {"failed", "reconciled_failed"}
    status = (
        "COMPLETED"
        if terminal_success
        else "FAILED_TERMINAL"
        if terminal_failure
        else "RECONCILIATION_REQUIRED"
        if latest.state == "outcome_unknown"
        else latest.state.upper()
    )
    timestamps = {
        "intent": events[0].created_at.isoformat(),
        "policy": events[0].created_at.isoformat(),
        "lease": events[0].created_at.isoformat(),
    }
    for event in events:
        if event.state == "started":
            timestamps["dispatched"] = event.created_at.isoformat()
        if event.state in {"succeeded", "reconciled_succeeded"}:
            timestamps["consequence"] = event.created_at.isoformat()
    if audit is not None:
        timestamps["receipt"] = audit.created_at.isoformat()

    return {
        "execution_id": execution_id,
        "status": status,
        "lease_scope": f"{latest.action}:{latest.resource}",
        "audit_id": audit.log_id if audit else None,
        "evidence_hash": latest.completion_proof_ref,
        "audit_hash": audit.log_hash if audit else None,
        "local_ledger_id": payment.tx_hash if payment else None,
        "settlement_network": payment.chain_id if payment else None,
        "budget_hold_status": hold.status.value if hold else None,
        "rationale": "Cryptographic lease and exact connector scope verified by the governed target.",
        "timestamps": timestamps,
        # Read-only release gate: mutation endpoints are not yet certified.
        "can_cancel": False,
        "can_retry": False,
        "external_pgl_verified": False,
        "onchain_x402_verified": False,
    }


@router.get("/latest")
def latest_execution(request: Request, db: Session = Depends(get_session)) -> dict:
    workspace_id = str(request.scope["auth_workspace"])
    latest = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.principal == workspace_id)
        .order_by(ConsequenceExecutionEvent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        raise HTTPException(status_code=404, detail="No governed n8n execution exists")
    return _project(db, latest.execution_id, workspace_id)


@router.get("/{execution_id}")
def execution_projection(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> dict:
    return _project(db, execution_id, str(request.scope["auth_workspace"]))


__all__ = ["router"]
