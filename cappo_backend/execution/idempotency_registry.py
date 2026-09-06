import hashlib
import json
from enum import Enum
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cappo_backend.db.session import SessionLocal
from cappo_backend.models.n8n_governed_target_models import ExecutionRegistry


class ExecutionState(str, Enum):
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCELLED = "CANCELLED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

class IdempotencyRegistry:
    def __init__(self, db_path: str = None):
        # We ignore db_path and rely on SQLAlchemy config
        pass

    def _hash_action(self, action_data: dict) -> str:
        serialized = json.dumps(action_data, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def reserve(self, jti: str, execution_id: str, action_data: dict) -> tuple[bool, Optional[str], Optional[dict]]:
        action_hash = self._hash_action(action_data)
        
        with SessionLocal() as session:
            try:
                # We need atomic operations; in Postgres we could use INSERT ... ON CONFLICT
                # But since we support both, we do a selective read/write with transaction isolation.
                
                # In SQLAlchemy, we can explicitly lock the row if it exists, or insert.
                existing = session.query(ExecutionRegistry).filter(
                    (ExecutionRegistry.execution_id == execution_id) | (ExecutionRegistry.jti == jti)
                ).with_for_update().first()
                
                if existing:
                    if existing.jti != jti or existing.execution_id != execution_id:
                        return False, "DENY: Two distinct jti values cannot reuse one execution_id inconsistently.", None
                        
                    if existing.action_hash != action_hash:
                        return False, "DENY: Same jti, altered payload is denied.", None
                    
                    result = existing.get_result()
                    state = ExecutionState(existing.state)
                    
                    if state == ExecutionState.SUCCEEDED:
                        return False, None, result
                    elif state in (ExecutionState.RESERVED, ExecutionState.RUNNING):
                        return False, "DENY: Execution is already running (concurrent duplicate).", None
                    elif state == ExecutionState.FAILED_TERMINAL:
                        return False, "DENY: Execution previously failed terminally.", None
                    elif state == ExecutionState.FAILED_RETRYABLE:
                        existing.state = ExecutionState.RESERVED.value
                        session.commit()
                        return True, None, None
                else:
                    new_exec = ExecutionRegistry(
                        jti=jti,
                        execution_id=execution_id,
                        action_hash=action_hash,
                        state=ExecutionState.RESERVED.value
                    )
                    session.add(new_exec)
                    session.commit()
                    return True, None, None
                    
            except IntegrityError as e:
                session.rollback()
                return False, f"Database constraint error: {e}", None
            except Exception as e:
                session.rollback()
                return False, f"Database error: {e}", None

    def update_state(self, execution_id: str, state: ExecutionState, result: Optional[dict] = None) -> bool:
        with SessionLocal() as session:
            try:
                exec_record = session.query(ExecutionRegistry).filter_by(execution_id=execution_id).first()
                if exec_record:
                    exec_record.state = state.value
                    if result:
                        exec_record.set_result(result)
                    session.commit()
                    return True
                return False
            except Exception:
                session.rollback()
                return False

    def get_state(self, execution_id: str):
        with SessionLocal() as session:
            exec_record = session.query(ExecutionRegistry).filter_by(execution_id=execution_id).first()
            if not exec_record:
                return None, None
            return ExecutionState(exec_record.state), exec_record.get_result()
