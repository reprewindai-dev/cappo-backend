"""Audit trail query API.

Mirrors the cAPI /api/audit endpoint functionality.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.audit_event import AuditEvent

router = APIRouter(prefix="/v1/audit")

@router.get("/ledger")
def query_audit_trail(
    agent_id: str | None = Query(None, alias="agent_id"),
    operation_type: str | None = Query(None, alias="status"),
    workspace_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Query the audit trail with filters.

    Accepts optional query parameters: `agent_id` (run_id), `status` (operation_type),
    `workspace_id`, and `limit`.
    """
    stmt = select(AuditEvent).order_by(desc(AuditEvent.created_at))

    if agent_id:
        stmt = stmt.where(AuditEvent.run_id == agent_id)
    if operation_type:
        stmt = stmt.where(AuditEvent.operation_type == operation_type)
    if workspace_id:
        stmt = stmt.where(AuditEvent.workspace_id == workspace_id)

    stmt = stmt.limit(limit)
    events = db.execute(stmt).scalars().all()

    return {
        "total": len(events),
        "query": {
            "agent_id": agent_id,
            "operation_type": operation_type,
            "workspace_id": workspace_id,
            "limit": limit,
        },
        "records": [
            {
                "id": e.log_id,
                "operation_type": e.operation_type,
                "workspace_id": e.workspace_id,
                "run_id": e.run_id,
                "payload": e.payload,
                "log_hash": e.log_hash,
                "previous_log_hash": e.previous_log_hash,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


@router.get("/verify")
def verify_ledger(db: Session = Depends(get_session)) -> dict[str, Any]:
    """Verify the integrity of all ledger chains."""
    from cappo_backend.services.ledger_verifier import LedgerVerifier
    return LedgerVerifier(db).verify_all()
