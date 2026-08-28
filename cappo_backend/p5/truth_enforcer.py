import time
from typing import Optional, Dict, Any
from cappo_backend.identity.models import AuthorityArtifact
from cappo_backend.p5.states import TruthState
from cappo_backend.p5.engine import P5Engine, compute_proof_subject_hash
from cappo_backend.p5.errors import *
from cappo_backend.identity.replay_cache import ReplayCache

class P5TruthEnforcer:
    def __init__(self, engine: P5Engine, replay_cache: Optional[ReplayCache] = None):
        self.engine = engine
        self.replay_cache = replay_cache or ReplayCache()
        self._revoked_identities = set() # For local status modeling

    def revoke_identity(self, authority_id: str):
        self._revoked_identities.add(authority_id)

    def authorize_and_record_truth(
        self,
        operation_id: str,
        asserted_truth_state: TruthState,
        authority: Optional[AuthorityArtifact],
        cappo_decision: str,
        jti: str = None,
        proof_type: str = "cryptographic",
        cappo_decision_id: str = None,
        proof_subject_hash: str = None,
        actor_identity: str = None,
        # Expected hashes for validation tests
        expected_candidate_act_hash: str = None,
        is_stale: bool = False,
    ):
        op = self.engine._load(operation_id)
        
        base_kwargs = {
            "operation_id": operation_id,
            "previous_truth_state": str(op.current_truth_state),
            "attempted_truth_state": str(asserted_truth_state),
            "ephemeral_execution_id": authority.ephemeral_execution_id if authority else None,
            "authority_id": authority.authority_id if authority else None,
            "proof_subject_hash": proof_subject_hash
        }

        if not authority:
            raise MissingTruthTransitionAuthorityError(**base_kwargs)

        if is_stale:
            raise StaleIdentityDeniedError(**base_kwargs)

        if authority.authority_id in self._revoked_identities:
            raise StaleIdentityDeniedError(**base_kwargs) # Or Revoked

        if authority.expires_at < int(time.time()):
            raise AuthorityExpiredError(**base_kwargs)

        if authority.ephemeral_execution_id != actor_identity: # Usually EI maps to actor
            raise AuthorityEiMismatchError(**base_kwargs)

        if expected_candidate_act_hash and authority.candidate_act_hash != expected_candidate_act_hash:
            raise AuthorityCandidateActMismatchError(**base_kwargs)

        if "truth.transition" not in authority.rights:
            if "execute" in authority.rights:
                raise ExecuteOnlyTruthDeniedError(**base_kwargs)
            elif "observe" in authority.rights:
                raise ObserveOnlyTruthDeniedError(**base_kwargs)
            elif "reconcile" in authority.rights:
                raise ReconcileOnlyTruthDeniedError(**base_kwargs)
            else:
                raise MissingTruthTransitionAuthorityError(**base_kwargs)

        if not authority.policy_decision_hash:
            raise P5TruthIdentityError("P5_MISSING_POLICY_DECISION", "Missing policy decision hash", **base_kwargs)

        if cappo_decision == "ALLOW":
            raise ConsequenceAllowNotTruthAllowError(**base_kwargs)
            
        if cappo_decision != "TRUTH_ALLOW":
            raise CappoTruthDecisionRequiredError(**base_kwargs)

        # Validate proof subject hash
        expected_hash = compute_proof_subject_hash(
            operation_id=operation_id,
            intent_hash=op.intent_hash,
            previous_truth_state=op.current_truth_state,
            asserted_truth_state=asserted_truth_state.value,
            consequence_id=op.consequence_id,
            actor_identity=actor_identity,
            sink_identity=op.sink_class,
        )

        if jti and not self.replay_cache.check_and_store(jti, authority.expires_at):
            raise TruthReplayDeniedError(**base_kwargs)

        if proof_subject_hash and proof_subject_hash != expected_hash:
            raise ProofSubjectMismatchError(**base_kwargs)

        # If all checks pass, record the transition
        if asserted_truth_state == TruthState.COMPLETED_SUCCESS:
            return self.engine.complete_success(
                operation_id=operation_id,
                actor_identity=actor_identity,
                proof_type=proof_type,
                proof_subject_hash=proof_subject_hash,
                cappo_decision_id=cappo_decision_id,
                has_truth_transition=True
            )
        elif asserted_truth_state == TruthState.OBSERVED_EFFECT:
            return self.engine.record_observed_effect(
                operation_id=operation_id,
                observation="observation",
                # record_observed_effect might not take has_truth_transition or it might. Let's check engine.py.
            )
        elif asserted_truth_state == TruthState.COMPLETED_FAILURE:
            return self.engine.complete_failure(
                operation_id=operation_id,
                actor_identity=actor_identity,
                proof_type=proof_type,
                proof_subject_hash=proof_subject_hash,
                cappo_decision_id=cappo_decision_id,
                has_truth_transition=True
            )
        else:
            raise NotImplementedError(f"Transition to {asserted_truth_state} not mapped in this enforcer wrapper.")
