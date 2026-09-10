from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProofProfile(str, Enum):
    BASIC_V1 = "fedcom/basic-v1"
    SANDBOXED_V1 = "fedcom/sandboxed-v1"
    ATTESTED_V1 = "fedcom/attested-v1"
    FINALITY_V1 = "fedcom/finality-v1"

class AdmissionDecision(str, Enum):
    ADMITTED = "ADMITTED"
    DENIED = "DENIED"

class DecisionReasonCode(str, Enum):
    NONE = "NONE"
    E_EXPIRED = "E_EXPIRED"
    E_REPLAY = "E_REPLAY"
    E_REVOKED = "E_REVOKED"
    E_SCOPE = "E_SCOPE"
    E_TARGET = "E_TARGET"
    E_WORKLOAD = "E_WORKLOAD"
    E_POLICY = "E_POLICY"
    E_REPRESENTATION = "E_REPRESENTATION"
    E_JURISDICTION = "E_JURISDICTION"
    E_EXECUTOR_UNAVAILABLE = "E_EXECUTOR_UNAVAILABLE"
    E_BUDGET = "E_BUDGET"
    E_PROOF_PROFILE = "E_PROOF_PROFILE"
    E_ATTESTATION = "E_ATTESTATION"

class VeklomExecutionEnvelope(BaseModel):
    """
    Minimum Viable Envelope E: Binds Intent to Authority.
    """
    envelope_id: str               # The new global correlation ID
    issuer: str                    # e.g., did:agent:B
    audience: str                  # e.g., did:node:A
    execution_identity: str        # The legacy execution_id (biscuit capability)
    workload_hash: str             # SHA-256 of the prompt/action payload to prevent MITM tampering
    issued_at: datetime
    expires_at: datetime
    signature: str                 # RFC 9421 Signature over the above fields

class VeklomTransitionReceipt(BaseModel):
    """
    Minimum Viable Receipt R: Binds Execution to Settlement.
    """
    receipt_id: str                # The x402 settlement receipt
    envelope_id: str               # Correlates back to Intent
    admission_decision: AdmissionDecision
    workload_hash: str             # Proves exactly what was executed/denied
    executor_identity: str         # The node that executed it (did:node:A)
    output_commitment: Optional[str] = None # Hash of the generated output (if admitted)
    signature: str                 # RFC 9421 Signature proving the executor's claim

