# ruff: noqa
import time
from uuid import uuid4

import pytest

from cappo_backend.identity.models import AuthorityArtifact
from cappo_backend.p5.engine import P5Engine, compute_proof_subject_hash
from cappo_backend.p5.errors import *
from cappo_backend.p5.states import SinkClass, TruthState
from cappo_backend.p5.truth_enforcer import P5TruthEnforcer


@pytest.fixture
def engine(db):
    return P5Engine(db)

@pytest.fixture
def enforcer(engine):
    return P5TruthEnforcer(engine)

def create_mock_authority(
    operation_id="op_1",
    rights=["execute", "truth.transition"],
    expires_at=None,
    candidate_act_hash="mock_act_hash",
    authority_id="mock_auth_id",
    ephemeral_execution_id="mock_ei"
):
    return AuthorityArtifact(
        authority_id=authority_id,
        ephemeral_execution_id=ephemeral_execution_id,
        scope_hash="mock_scope_hash",
        policy_decision_hash="mock_decision_hash",
        candidate_act_hash=candidate_act_hash,
        destination_hash="mock_destination_hash",
        rights=rights,
        issued_at=int(time.time()),
        expires_at=expires_at or int(time.time()) + 3600,
        proof_of_possession="mock_pop"
    )

def test_timeout_becomes_outcome_unknown(engine):
    op = engine.create_operation("op_1", "consequence_1", SinkClass.A_TRANSACTIONAL_LOCAL, "intent_123", "mock_ei")
    engine.authorize(op.operation_id, "mock_auth_id")
    engine.start_execution(op.operation_id, "mock_ei")
    engine.record_outcome_unknown(op.operation_id, "timeout")
    assert engine._load(op.operation_id).current_truth_state == TruthState.OUTCOME_UNKNOWN

def test_crash_after_side_effect_becomes_outcome_unknown(engine):
    op = engine.create_operation("op_2", "consequence_2", SinkClass.B_IDEMPOTENT_EXTERNAL, "intent_123", "mock_ei")
    engine.authorize(op.operation_id, "mock_auth_id")
    engine.start_execution(op.operation_id, "mock_ei")
    engine.record_outcome_unknown(op.operation_id, "crash")
    assert engine._load(op.operation_id).current_truth_state == TruthState.OUTCOME_UNKNOWN

def test_proof_transplant_denied(engine, enforcer):
    op_A = engine.create_operation("op_A", "consequence_A", SinkClass.A_TRANSACTIONAL_LOCAL, "intent_A", "mock_ei")
    op_B = engine.create_operation("op_B", "consequence_B", SinkClass.A_TRANSACTIONAL_LOCAL, "intent_B", "mock_ei")
    
    engine.authorize(op_A.operation_id, "mock_auth_id")
    engine.authorize(op_B.operation_id, "mock_auth_id")
    engine.start_execution(op_A.operation_id, "mock_ei")
    engine.start_execution(op_B.operation_id, "mock_ei")

    auth_A = create_mock_authority(operation_id=op_A.operation_id)
    
    # Compute proof hash for operation A
    proof_hash_A = compute_proof_subject_hash(
        operation_id=op_A.operation_id,
        intent_hash=op_A.intent_hash,
        candidate_act_hash=auth_A.candidate_act_hash,
        authority_id=auth_A.authority_id,
        execution_identity="mock_ei",
        sink_id=getattr(op_A.sink_class, "value", str(op_A.sink_class)),
        previous_truth_state=str(TruthState.EXECUTION_STARTED.value),
        asserted_truth_state=str(TruthState.COMPLETED_SUCCESS.value),
        consequence_identity=str(op_A.consequence_id),
        proof_type="cryptographic"
    )

    # Attempt to use proof for operation A to complete operation B
    with pytest.raises(ProofSubjectMismatchError):
        enforcer.authorize_and_record_truth(
            operation_id=op_B.operation_id,
            asserted_truth_state=TruthState.COMPLETED_SUCCESS,
            authority=auth_A,
            cappo_decision="TRUTH_ALLOW",
            proof_subject_hash=proof_hash_A,
            actor_identity="mock_ei"
        )

def test_worker_without_truth_transition_tries_to_mark_success(engine, enforcer):
    op = engine.create_operation("op_3", "consequence_3", SinkClass.A_TRANSACTIONAL_LOCAL, "intent_123", "mock_ei")
    engine.authorize(op.operation_id, "mock_auth_id")
    engine.start_execution(op.operation_id, "mock_ei")

    auth = create_mock_authority(operation_id="op_3", rights=["execute"]) # Missing truth.transition
    
    with pytest.raises(ExecuteOnlyTruthDeniedError):
        enforcer.authorize_and_record_truth(
            operation_id=op.operation_id,
            asserted_truth_state=TruthState.COMPLETED_SUCCESS,
            authority=auth,
            cappo_decision="TRUTH_ALLOW",
            actor_identity="mock_ei"
        )

def test_expired_authority_tries_to_transition_truth(engine, enforcer):
    op = engine.create_operation("op_4", "consequence_4", SinkClass.A_TRANSACTIONAL_LOCAL, "intent_123", "mock_ei")
    engine.authorize(op.operation_id, "mock_auth_id")
    engine.start_execution(op.operation_id, "mock_ei")

    auth = create_mock_authority(operation_id="op_4", expires_at=int(time.time()) - 100) # Expired
    
    with pytest.raises(AuthorityExpiredError):
        enforcer.authorize_and_record_truth(
            operation_id=op.operation_id,
            asserted_truth_state=TruthState.COMPLETED_SUCCESS,
            authority=auth,
            cappo_decision="TRUTH_ALLOW",
            actor_identity="mock_ei"
        )

def test_replay_valid_proof_denied(engine, enforcer):
    op = engine.create_operation("op_5", "consequence_5", SinkClass.A_TRANSACTIONAL_LOCAL, "intent_123", "mock_ei")
    engine.authorize(op.operation_id, "mock_auth_id")
    engine.start_execution(op.operation_id, "mock_ei")

    auth = create_mock_authority(operation_id=op.operation_id)
    proof_hash = compute_proof_subject_hash(
        operation_id=op.operation_id,
        intent_hash=op.intent_hash,
        candidate_act_hash=auth.candidate_act_hash,
        authority_id=auth.authority_id,
        execution_identity="mock_ei",
        sink_id=getattr(op.sink_class, "value", str(op.sink_class)),
        previous_truth_state=str(TruthState.EXECUTION_STARTED.value),
        asserted_truth_state=str(TruthState.COMPLETED_SUCCESS.value),
        consequence_identity=str(op.consequence_id),
        proof_type="cryptographic"
    )

    enforcer.authorize_and_record_truth(
        operation_id=op.operation_id,
        asserted_truth_state=TruthState.COMPLETED_SUCCESS,
        authority=auth,
        cappo_decision="TRUTH_ALLOW",
        jti="jti_123",
        proof_subject_hash=proof_hash,
        actor_identity="mock_ei"
    )

    # Replay same proof JTI
    with pytest.raises(TruthReplayDeniedError):
        enforcer.authorize_and_record_truth(
            operation_id=op.operation_id,
            asserted_truth_state=TruthState.COMPLETED_SUCCESS,
            authority=auth,
            cappo_decision="TRUTH_ALLOW",
            jti="jti_123",
            proof_subject_hash=proof_hash,
            actor_identity="mock_ei"
        )

def test_observed_world_state_exists_but_attribution_missing(engine, enforcer):
    op = engine.create_operation("op_6", "consequence_6", SinkClass.C_QUERYABLE_EXTERNAL, "intent_123", "mock_ei")
    engine.authorize(op.operation_id, "mock_auth_id")
    engine.start_execution(op.operation_id, "mock_ei")

    auth = create_mock_authority(operation_id=op.operation_id, rights=["observe"])
    
    # Observe only allowed, meaning it CANNOT mark completed success
    with pytest.raises(ObserveOnlyTruthDeniedError):
        enforcer.authorize_and_record_truth(
            operation_id=op.operation_id,
            asserted_truth_state=TruthState.COMPLETED_SUCCESS,
            authority=auth,
            cappo_decision="TRUTH_ALLOW",
            actor_identity="mock_ei"
        )
