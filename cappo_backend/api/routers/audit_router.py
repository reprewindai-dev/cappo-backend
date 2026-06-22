"""Audit/ledger verification endpoints.

EI Plan §Rollout Phase 4 ("structured LAW 0 audit logging"). Exposes read-only
integrity checks over the hash-chained audit ledger and the per-certificate PGL
ledgers. These let an operator (or an external monitor) confirm the ledgers have
not been tampered with after the fact.

These endpoints only read and re-derive hashes; they never mutate state. Authn/
authz for operator routes is layered ahead of them (see main.py middleware notes).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.services.ledger_verifier import LedgerVerifier

router = APIRouter(prefix="/v1/audit")


@router.get("/verify")
def verify_ledgers(db: Session = Depends(get_session)) -> dict[str, object]:
    """Verify the global audit chain and every per-certificate PGL chain."""
    return LedgerVerifier(db).verify_all()


@router.get("/verify/audit")
def verify_audit_chain(db: Session = Depends(get_session)) -> dict[str, object]:
    """Verify only the global audit event chain."""
    return LedgerVerifier(db).verify_audit_chain().as_dict()


@router.get("/verify/pgl/{certificate_id}")
def verify_pgl_chain(certificate_id: str, db: Session = Depends(get_session)) -> dict[str, object]:
    """Verify the PGL ledger chain for a single certificate."""
    return LedgerVerifier(db).verify_pgl_chain(certificate_id).as_dict()


@router.get("/ledger/traces")
def get_ledger_traces(
    db: Session = Depends(get_session), limit: int = 50, offset: int = 0
) -> dict[str, object]:
    """Fetch the latest governed execution traces for the audit explorer."""
    from cappo_backend.models.execution import GovernedRun

    runs = (
        db.query(GovernedRun)
        .order_by(GovernedRun.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    traces = []
    for r in runs:
        traces.append(
            {
                "run_id": r.run_id,
                "execution_id": r.execution_identity.get("execution_id")
                if r.execution_identity
                else None,
                "agent_id": r.agent_id,
                "prompt": r.request_payload.get("prompt", "") if r.request_payload else "",
                "response": r.response_payload.get("response", "") if r.response_payload else "",
                "latency_ms": r.latency_ms,
                "cost_cents": r.cost_cents,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )

    return {"traces": traces}
