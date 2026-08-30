from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict

class ClaimState(str, Enum):
    CLAIMED = "CLAIMED"
    AUTHENTICATED = "AUTHENTICATED"
    CURRENT = "CURRENT"
    AUTHORITATIVE = "AUTHORITATIVE"
    CORROBORATED = "CORROBORATED"
    ADMISSIBLE = "ADMISSIBLE"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTED = "CONFLICTED"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"

class FactRequirement(BaseModel):
    fact_domain: str
    minimum_assurance: str
    max_age_seconds: int
    authority_class: Optional[str] = None
    corroboration_required: bool = False

class TypedPayload(BaseModel):
    subject: str
    predicate: str
    value: Any
    unit: Optional[str] = None
    scope: str
    effective_from: Optional[int] = None
    effective_until: Optional[int] = None

class LineageReceipt(BaseModel):
    source_ids: List[str]
    source_versions: List[int]
    ordered_chunk_ids: List[str]
    transformation_function: str
    transformation_parameters: Dict[str, Any]
    output_digest: str
    policy_used: str

class TruthClaim(BaseModel):
    claim_id: str
    source_id: str
    source_domain: str
    tenant_id: str
    fact_type: str
    
    version: int
    parent_version: Optional[int] = None
    
    signer: str
    signature: str
    
    payload: TypedPayload
    lineage: Optional[LineageReceipt] = None
    
    evaluation_time_locked: int
    expires_at: int
    
    state: ClaimState = ClaimState.CLAIMED
    resolution_reason: Optional[str] = None
