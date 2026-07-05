from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import hashlib
import json

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class CovenantPhase(str, Enum):
    IDENTITY = "1_identity"
    POLICY = "2_policy"
    SAFETY = "3_safety"
    COST = "4_cost"
    APPROVAL = "5_approval"
    EXECUTION = "6_execution"
    EVIDENCE = "7_evidence"
    AUDIT = "8_audit"
    RESPONSE = "9_response"

class QuarantineStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"

class ExecutionReceipt(BaseModel):
    receipt_id: str
    tenant_id: str
    agent_id: str
    tool_name: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    arguments: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    risk_level: RiskLevel
    vnp_stake_consumed: float = 0.0
    evidence_hash: str = ""
    status: str = "success"

class EvidenceLedgerRecord(BaseModel):
    record_id: str
    previous_hash: str
    timestamp: str
    receipt: ExecutionReceipt
    signature: str = ""

    def generate_hash(self) -> str:
        """Generates a SHA-256 hash of the deterministic JSON representation of this record."""
        # We exclude the signature from the hash computation of the payload itself
        payload = {
            "record_id": self.record_id,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "receipt": self.receipt.model_dump(mode="json")
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

class QuarantineRequest(BaseModel):
    quarantine_id: str
    tenant_id: str
    agent_id: str
    tool_name: str
    risk_level: RiskLevel
    arguments: Dict[str, Any]
    status: QuarantineStatus = QuarantineStatus.PENDING
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    reason: str
