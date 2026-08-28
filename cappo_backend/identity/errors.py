class IdentityValidationError(Exception):
    """Base class for all identity validation errors."""
    def __init__(self, error_code: str, reason: str, route: str, method: str, trace_id: str, workload_identifier: str = None, ephemeral_execution_id: str = None):
        super().__init__(f"{error_code}: {reason}")
        self.error_code = error_code
        self.reason = reason
        self.route = route
        self.method = method
        self.trace_id = trace_id
        self.workload_identifier = workload_identifier
        self.ephemeral_execution_id = ephemeral_execution_id
        
    def to_evidence(self):
        import time
        return {
            "error_code": self.error_code,
            "reason": self.reason,
            "route": self.route,
            "method": self.method,
            "trace_id": self.trace_id,
            "workload_identifier": self.workload_identifier,
            "ephemeral_execution_id": self.ephemeral_execution_id,
            "timestamp": int(time.time())
        }

class MissingWorkloadIdentityError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_MISSING_WORKLOAD_IDENTITY", "Workload-Identity header is missing", **kwargs)

class MissingExecutionContextError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_MISSING_EXECUTION_CONTEXT", "Execution-Context header is missing", **kwargs)

class MissingWorkloadProofError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_MISSING_WORKLOAD_PROOF", "Workload-Proof header is missing", **kwargs)

class MissingAuthorityError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_MISSING_AUTHORITY", "Veklom-Authority header is missing", **kwargs)

class TokenExpiredError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_TOKEN_EXPIRED", "Token is expired", **kwargs)

class AudienceMismatchError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_AUDIENCE_MISMATCH", "Token audience does not match expected API", **kwargs)

class BodyHashMismatchError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_BODY_HASH_MISMATCH", "Request body hash does not match token", **kwargs)

class RequestBindingMismatchError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_REQUEST_BINDING_MISMATCH", "Method or URI does not match proof token", **kwargs)

class CandidateActMismatchError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_CANDIDATE_ACT_MISMATCH", "Candidate act hash mismatch", **kwargs)

class AuthorityHashMismatchError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_AUTHORITY_HASH_MISMATCH", "Authority hash mismatch", **kwargs)

class ReplayDeniedError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_REPLAY_DENIED", "Token JTI has been replayed", **kwargs)

class MalformedWorkloadIdentifierError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_MALFORMED_WORKLOAD_IDENTIFIER", "Workload identifier is malformed", **kwargs)

class ProfileOnlyDeniedError(IdentityValidationError):
    def __init__(self, **kwargs):
        super().__init__("WID_PROFILE_ONLY_DENIED", "Profile-only authority is denied for this action", **kwargs)

