from dataclasses import dataclass
from typing import List, Optional, Dict, Any

@dataclass
class WorkloadIdentityToken:
    iss: str
    sub: str  # workload identifier
    aud: str
    exp: int
    iat: int
    jti: str
    cnf: Dict[str, Any]
    trust_domain: Optional[str] = None
    profile_id: Optional[str] = None

@dataclass
class ExecutionContextToken:
    iss: str
    sub: str
    aud: str
    exp: int
    iat: int
    jti: str
    ephemeral_execution_id: str
    candidate_act_hash: str
    cnf: Dict[str, Any]
    workflow_id: Optional[str] = None
    task_id: Optional[str] = None
    intent_hash: Optional[str] = None
    p5_operation_id: Optional[str] = None

@dataclass
class WorkloadProofToken:
    htm: str
    htu: str
    body_hash: str
    wit_hash: str
    ect_hash: str
    authority_hash: str
    jti: str
    cnf: Dict[str, Any]
    exp: Optional[int] = None

@dataclass
class AuthorityArtifact:
    authority_id: str
    ephemeral_execution_id: str
    scope_hash: str
    policy_decision_hash: str
    candidate_act_hash: str
    destination_hash: str
    rights: List[str]
    issued_at: int
    expires_at: int
    proof_of_possession: str
    inbound_truth_state: str = "ADMISSIBLE"
    required_truth_state: str = "ADMISSIBLE"
