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
def verify_pgl_chain(
    certificate_id: str, db: Session = Depends(get_session)
) -> dict[str, object]:
    """Verify the PGL ledger chain for a single certificate."""
    return LedgerVerifier(db).verify_pgl_chain(certificate_id).as_dict()
