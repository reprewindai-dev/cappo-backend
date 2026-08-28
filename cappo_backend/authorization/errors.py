class CappoAuthorizationError(Exception):
    def __init__(self, error_code: str, reason: str, route: str, method: str, trace_id: str,
                 workload_identifier: str = None, ephemeral_execution_id: str = None,
                 candidate_act_hash: str = None, authority_id: str = None):
        super().__init__(f"{error_code}: {reason}")
        self.error_code = error_code
        self.denial_code = error_code
        self.reason = reason
        self.route = route
        self.method = method
        self.trace_id = trace_id
        self.workload_identifier = workload_identifier
        self.ephemeral_execution_id = ephemeral_execution_id
        self.candidate_act_hash = candidate_act_hash
        self.authority_id = authority_id
        
    def to_evidence(self):
        import time
        return {
            "error_code": self.error_code,
            "denial_code": self.denial_code,
            "reason": self.reason,
            "route": self.route,
            "method": self.method,
            "trace_id": self.trace_id,
            "workload_identifier": self.workload_identifier,
            "ephemeral_execution_id": self.ephemeral_execution_id,
            "candidate_act_hash": self.candidate_act_hash,
            "authority_id": self.authority_id,
            "timestamp": int(time.time()),
            "p5_state_unchanged": True
        }

class ProfileOnlyDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_PROFILE_ONLY_DENIED", "Profile-only authority is denied for this action", **kwargs)

class StaticServiceDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_STATIC_SERVICE_DENIED", "Static service name authority is denied", **kwargs)

class ApiKeyOnlyDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_API_KEY_ONLY_DENIED", "API key alone is denied for authorization", **kwargs)

class ClaimedRoleDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_CLAIMED_ROLE_DENIED", "Claimed role alone is denied", **kwargs)

class SourceIpOnlyDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_SOURCE_IP_ONLY_DENIED", "Source IP alone is denied", **kwargs)

class OperatorAssertionDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_OPERATOR_ASSERTION_DENIED", "Operator assertion alone is denied", **kwargs)

class TenantIdOnlyDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_TENANT_ID_ONLY_DENIED", "Tenant ID alone is denied", **kwargs)

class WorkloadIdentifierOnlyDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_WORKLOAD_IDENTIFIER_ONLY_DENIED", "Workload identifier alone is denied", **kwargs)

class MissingEphemeralExecutionIdError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_MISSING_EPHEMERAL_EXECUTION_ID", "Missing ephemeral execution ID", **kwargs)

class AuthorityEiMismatchError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_AUTHORITY_EI_MISMATCH", "Authority ephemeral execution ID mismatch", **kwargs)

class CandidateActMismatchError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_CANDIDATE_ACT_MISMATCH", "Candidate act hash mismatch", **kwargs)

class AuthorityHashMismatchError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_AUTHORITY_HASH_MISMATCH", "Authority hash mismatch", **kwargs)

class ScopeHashMismatchError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_SCOPE_HASH_MISMATCH", "Scope hash mismatch", **kwargs)

class DestinationHashMismatchError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_DESTINATION_HASH_MISMATCH", "Destination hash mismatch", **kwargs)

class PolicyDecisionHashMismatchError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_POLICY_DECISION_HASH_MISMATCH", "Policy decision hash mismatch", **kwargs)

class AuthorityExpiredError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_AUTHORITY_EXPIRED", "Authority is expired", **kwargs)

class ReplayDeniedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_REPLAY_DENIED", "Token JTI has been replayed", **kwargs)

class RightNotGrantedError(CappoAuthorizationError):
    def __init__(self, **kwargs):
        super().__init__("CAPPO_RIGHT_NOT_GRANTED", "Requested right is not granted", **kwargs)
