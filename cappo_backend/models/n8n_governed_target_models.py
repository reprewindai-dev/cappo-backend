import enum
import json
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy import Enum as SQLEnum

from cappo_backend.db.base import Base


class ExecutionState(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCELLED = "CANCELLED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

class ExecutionRegistry(Base):
    __tablename__ = "execution_registry"
    
    jti = Column(String, unique=True, nullable=False, index=True)
    execution_id = Column(String, primary_key=True, nullable=False)
    action_hash = Column(String, nullable=False)
    state = Column(String, nullable=False) # Store string for backward compatibility, handle parsing
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def set_result(self, result_dict):
        self.result = json.dumps(result_dict) if result_dict else None

    def get_result(self):
        return json.loads(self.result) if self.result else None

class RevocationPolicy(Base):
    __tablename__ = "revocation_policies"
    
    scope_type = Column(String, primary_key=True, nullable=False)
    scope_value = Column(String, primary_key=True, nullable=False)
    is_cancel = Column(Boolean, nullable=False, default=False)
    actor = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    audit_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class LeaseStateRecord(Base):
    __tablename__ = "lease_states"
    
    lease_id = Column(String, primary_key=True, nullable=False)
    state = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
