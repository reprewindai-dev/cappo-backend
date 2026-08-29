from enum import Enum
from typing import Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cappo_backend.db.session import SessionLocal
from cappo_backend.models.n8n_governed_target_models import RevocationPolicy, LeaseStateRecord

class LeaseState(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLING = "CANCELLING"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"

class RevocationRegistry:
    def __init__(self, db_path=None, fail_closed=True):
        # Ignore db_path, use SQLAlchemy session
        self.fail_closed = fail_closed

    def set_lease_state(self, lease_id: str, state: LeaseState):
        with SessionLocal() as session:
            try:
                record = session.query(LeaseStateRecord).filter_by(lease_id=lease_id).first()
                if record:
                    record.state = state.value
                else:
                    record = LeaseStateRecord(lease_id=lease_id, state=state.value)
                    session.add(record)
                session.commit()
            except Exception:
                session.rollback()

    def revoke(self, scope_type: str, scope_value: str, actor: str, reason: str, audit_id: str, is_cancel: bool = False):
        with SessionLocal() as session:
            try:
                policy = session.query(RevocationPolicy).filter_by(
                    scope_type=scope_type, scope_value=scope_value
                ).first()
                if policy:
                    policy.is_cancel = is_cancel
                    policy.actor = actor
                    policy.reason = reason
                    policy.audit_id = audit_id
                else:
                    policy = RevocationPolicy(
                        scope_type=scope_type,
                        scope_value=scope_value,
                        is_cancel=is_cancel,
                        actor=actor,
                        reason=reason,
                        audit_id=audit_id
                    )
                    session.add(policy)
                
                if scope_type == "LEASE":
                    new_state = LeaseState.CANCELLING if is_cancel else LeaseState.REVOKED
                    record = session.query(LeaseStateRecord).filter_by(lease_id=scope_value).first()
                    if record:
                        record.state = new_state.value
                    else:
                        session.add(LeaseStateRecord(lease_id=scope_value, state=new_state.value))
                
                session.commit()
            except Exception as e:
                session.rollback()
                raise RuntimeError(f"Failed to record revocation: {e}")

    def check_authority(self, kid: str, subject: str, lease_id: str, execution_id: str) -> LeaseState:
        with SessionLocal() as session:
            try:
                policies = session.query(RevocationPolicy).filter(
                    ( (RevocationPolicy.scope_type == 'GLOBAL') & (RevocationPolicy.scope_value == '*') ) |
                    ( (RevocationPolicy.scope_type == 'KID') & (RevocationPolicy.scope_value == kid) ) |
                    ( (RevocationPolicy.scope_type == 'SUBJECT') & (RevocationPolicy.scope_value == subject) ) |
                    ( (RevocationPolicy.scope_type == 'LEASE') & (RevocationPolicy.scope_value == lease_id) ) |
                    ( (RevocationPolicy.scope_type == 'EXECUTION') & (RevocationPolicy.scope_value == execution_id) )
                ).all()
                
                for p in policies:
                    if not p.is_cancel:
                        return LeaseState.REVOKED
                    elif p.is_cancel:
                        return LeaseState.CANCELLING

                row = session.query(LeaseStateRecord).filter_by(lease_id=lease_id).first()
                if row:
                    return LeaseState(row.state)
                    
                return LeaseState.ACTIVE
            except Exception as e:
                if self.fail_closed:
                    raise RuntimeError(f"Fail closed due to authority check error: {e}")
                return LeaseState.ACTIVE
