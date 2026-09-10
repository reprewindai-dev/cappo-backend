import time


class P5TruthIdentityError(Exception):
    def __init__(self, error_code: str, reason: str, operation_id: str,
                 previous_truth_state: str, attempted_truth_state: str,
                 ephemeral_execution_id: str = None, authority_id: str = None,
                 proof_subject_hash: str = None):
        super().__init__(f"{error_code}: {reason}")
        self.error_code = error_code
        self.denial_code = error_code
        self.reason = reason
        self.operation_id = operation_id
        self.previous_truth_state = previous_truth_state
        self.attempted_truth_state = attempted_truth_state
        self.ephemeral_execution_id = ephemeral_execution_id
        self.authority_id = authority_id
        self.proof_subject_hash = proof_subject_hash

    def to_evidence(self):
        return {
            "error_code": self.error_code,
            "denial_code": self.denial_code,
            "operation_id": self.operation_id,
            "previous_truth_state": self.previous_truth_state,
            "attempted_truth_state": self.attempted_truth_state,
            "ephemeral_execution_id": self.ephemeral_execution_id,
            "authority_id": self.authority_id,
            "proof_subject_hash": self.proof_subject_hash,
            "p5_state_unchanged": True,
            "timestamp": int(time.time()),
        }


class MissingTruthTransitionAuthorityError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_MISSING_TRUTH_TRANSITION_AUTHORITY", "Missing truth.transition authority", **kwargs)

class ExecuteOnlyTruthDeniedError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_EXECUTE_ONLY_TRUTH_DENIED", "Execute-only authority cannot assert truth", **kwargs)

class ObserveOnlyTruthDeniedError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_OBSERVE_ONLY_TRUTH_DENIED", "Observe-only authority cannot assert truth", **kwargs)

class ReconcileOnlyTruthDeniedError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_RECONCILE_ONLY_TRUTH_DENIED", "Reconcile-only authority cannot assert truth", **kwargs)

class AuthorityExpiredError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_AUTHORITY_EXPIRED", "Authority is expired", **kwargs)

class AuthorityEiMismatchError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_AUTHORITY_EI_MISMATCH", "Authority ephemeral execution ID mismatch", **kwargs)

class AuthorityCandidateActMismatchError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_AUTHORITY_CANDIDATE_ACT_MISMATCH", "Authority candidate act hash mismatch", **kwargs)

class ProofSubjectMismatchError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_PROOF_SUBJECT_MISMATCH", "Proof subject hash mismatch", **kwargs)

class CappoTruthDecisionRequiredError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_CAPPO_TRUTH_DECISION_REQUIRED", "CAPPO truth decision is required (TRUTH_ALLOW)", **kwargs)

class ConsequenceAllowNotTruthAllowError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_CONSEQUENCE_ALLOW_NOT_TRUTH_ALLOW", "CAPPO consequence ALLOW cannot substitute for TRUTH_ALLOW", **kwargs)

class StaleIdentityDeniedError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_STALE_IDENTITY_DENIED", "Stale identity denied", **kwargs)

class TruthReplayDeniedError(P5TruthIdentityError):
    def __init__(self, **kwargs):
        super().__init__("P5_TRUTH_REPLAY_DENIED", "Truth transition JTI replayed", **kwargs)
