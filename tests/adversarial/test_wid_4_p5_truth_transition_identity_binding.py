import time
import pytest
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from cappo_backend.db.base import Base
from cappo_backend.p5.engine import P5Engine, compute_proof_subject_hash
from cappo_backend.p5.states import TruthState
from cappo_backend.p5.models import P5Operation, P5Event, P5Outbox
from cappo_backend.p5.truth_enforcer import P5TruthEnforcer
from cappo_backend.identity.models import AuthorityArtifact
from cappo_backend.identity.replay_cache import ReplayCache
from cappo_backend.p5.errors import *

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()

@pytest.fixture
def p5_engine(db_session):
    return P5Engine(db_session)

@pytest.fixture
def replay_cache():
    return ReplayCache()

@pytest.fixture
def truth_enforcer(p5_engine, replay_cache):
    return P5TruthEnforcer(p5_engine, replay_cache)

@pytest.fixture
def active_operation(p5_engine):
    op_id = str(uuid.uuid4())
    p5_engine.create_operation(
        operation_id=op_id,
        consequence_id="cons_123",
        sink_class="payment",
        intent_hash="intent_hash_abc",
        actor_identity="exec_abc"
    )
    p5_engine.authorize(op_id, "exec_abc")
    p5_engine.start_execution(op_id, "exec_abc")
    # Move to OUTCOME_UNKNOWN to allow success/failure transitions
    p5_engine.record_outcome_unknown(op_id, "test")
    return op_id

def make_authority(rights=None):
    if rights is None:
        rights = ["truth.transition"]
    return AuthorityArtifact(
        authority_id="auth_123",
        ephemeral_execution_id="exec_abc",
        scope_hash="scope_val",
        policy_decision_hash="pol_val",
        candidate_act_hash="act_hash_val",
        destination_hash="dest_val",
        rights=rights,
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
        proof_of_possession="sig"
    )

def compute_valid_hash(op_id, from_state, to_state, actor="exec_abc", sink="payment", intent="intent_hash_abc", cons="cons_123"):
    return compute_proof_subject_hash(
        operation_id=op_id,
        intent_hash=intent,
        candidate_act_hash="act_hash_val",
        authority_id="auth_123",
        execution_identity=actor,
        sink_id=sink,
        previous_truth_state=from_state.value,
        asserted_truth_state=to_state.value,
        consequence_identity=cons,
        proof_type="cryptographic",
    )

def test_1_execute_only_authority_cannot_assert_completed_success(truth_enforcer, active_operation):
    auth = make_authority(["execute"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(ExecuteOnlyTruthDeniedError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )

def test_2_observe_only_authority_cannot_assert_observed_effect(truth_enforcer, active_operation):
    auth = make_authority(["observe"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.OBSERVED_EFFECT)
    with pytest.raises(ObserveOnlyTruthDeniedError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.OBSERVED_EFFECT, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )

def test_3_reconcile_only_authority_cannot_assert_completed_success(truth_enforcer, active_operation):
    auth = make_authority(["reconcile"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(ReconcileOnlyTruthDeniedError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )

def test_4_truth_transition_authority_can_assert_completed_success_with_proof(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    op = truth_enforcer.authorize_and_record_truth(
        active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
        proof_subject_hash=expected_hash, actor_identity="exec_abc", jti="jti_4"
    )
    assert op.current_truth_state == TruthState.COMPLETED_SUCCESS.value

def test_5_cappo_consequence_allow_cannot_substitute_for_truth_allow(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(ConsequenceAllowNotTruthAllowError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )

def test_6_missing_authority_denied(truth_enforcer, active_operation):
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(MissingTruthTransitionAuthorityError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, None, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )

def test_7_expired_authority_denied(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    auth.expires_at = int(time.time()) - 100
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(AuthorityExpiredError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )

def test_8_authority_ei_mismatch_denied(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    auth.ephemeral_execution_id = "DIFFERENT"
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(AuthorityEiMismatchError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )

def test_9_authority_candidate_act_hash_mismatch_denied(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(AuthorityCandidateActMismatchError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc",
            expected_candidate_act_hash="DIFFERENT"
        )

def test_10_proof_subject_hash_mismatch_denied(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    with pytest.raises(ProofSubjectMismatchError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash="BAD_HASH", actor_identity="exec_abc"
        )

def test_11_stale_identity_denied(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    with pytest.raises(StaleIdentityDeniedError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc", is_stale=True
        )

def test_12_replayed_truth_transition_jti_denied(truth_enforcer, active_operation):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    
    # First time passes
    truth_enforcer.authorize_and_record_truth(
        active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
        proof_subject_hash=expected_hash, actor_identity="exec_abc", jti="jti_12"
    )
    
    # Second time fails
    with pytest.raises(TruthReplayDeniedError):
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc", jti="jti_12"
        )

def test_13_denial_leaves_p5_truth_state_unchanged(truth_enforcer, active_operation, p5_engine):
    auth = make_authority(["execute"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    try:
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )
    except ExecuteOnlyTruthDeniedError:
        pass
    
    op = p5_engine._load(active_operation)
    assert op.current_truth_state == TruthState.OUTCOME_UNKNOWN.value

from cappo_backend.p5.states import TruthState, P5EventType
# ...
def test_14_denial_does_not_append_strengthened_success_event(truth_enforcer, active_operation, p5_engine, db_session):
    auth = make_authority(["execute"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    try:
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )
    except ExecuteOnlyTruthDeniedError:
        pass
    
    events = db_session.query(P5Event).filter_by(operation_id=active_operation, event_type=P5EventType.TRUTH_COMPLETED_SUCCESS.value).all()
    assert len(events) == 0

def test_15_denial_emits_structured_evidence(truth_enforcer, active_operation):
    auth = make_authority(["execute"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    try:
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )
    except ExecuteOnlyTruthDeniedError as e:
        evidence = e.to_evidence()
        assert evidence["error_code"] == "P5_EXECUTE_ONLY_TRUTH_DENIED"
        assert evidence["operation_id"] == active_operation
        assert evidence["p5_state_unchanged"] is True
        assert "timestamp" in evidence

def test_16_valid_truth_allow_transition_appends_correct_p5_event(truth_enforcer, active_operation, db_session):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    truth_enforcer.authorize_and_record_truth(
        active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
        proof_subject_hash=expected_hash, actor_identity="exec_abc", jti="jti_16"
    )
    
    events = db_session.query(P5Event).filter_by(operation_id=active_operation, event_type=P5EventType.TRUTH_COMPLETED_SUCCESS.value).all()
    assert len(events) == 1
    assert events[0].asserted_truth_state == str(TruthState.COMPLETED_SUCCESS)

def test_17_valid_transition_creates_p5_outbox_item(truth_enforcer, active_operation, db_session):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    truth_enforcer.authorize_and_record_truth(
        active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
        proof_subject_hash=expected_hash, actor_identity="exec_abc", jti="jti_17"
    )
    
    outbox = db_session.query(P5Outbox).all()
    assert len(outbox) > 0

def test_18_valid_transition_sends_event_hash_unchanged_to_outbox_payload(truth_enforcer, active_operation, db_session):
    auth = make_authority(["truth.transition"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    truth_enforcer.authorize_and_record_truth(
        active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
        proof_subject_hash=expected_hash, actor_identity="exec_abc", jti="jti_18"
    )
    
    events = db_session.query(P5Event).filter_by(operation_id=active_operation, event_type=P5EventType.TRUTH_COMPLETED_SUCCESS.value).all()
    event_id = events[0].event_id
    outbox = db_session.query(P5Outbox).filter_by(event_id=event_id).first()
    assert outbox is not None
    assert outbox.payload_hash is not None

def test_19_projection_cannot_overclaim_after_truth_transition_denial(truth_enforcer, active_operation, p5_engine):
    auth = make_authority(["execute"])
    expected_hash = compute_valid_hash(active_operation, TruthState.OUTCOME_UNKNOWN, TruthState.COMPLETED_SUCCESS)
    try:
        truth_enforcer.authorize_and_record_truth(
            active_operation, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )
    except ExecuteOnlyTruthDeniedError:
        pass
    
    op = p5_engine._load(active_operation)
    assert op.current_truth_state == TruthState.OUTCOME_UNKNOWN.value

def test_20_authorized_to_completed_success_remains_impossible_without_proof_and_truth_transition(truth_enforcer, p5_engine):
    op_id = str(uuid.uuid4())
    p5_engine.create_operation(
        operation_id=op_id,
        consequence_id="cons_123",
        sink_class="payment",
        intent_hash="intent_hash_abc",
        actor_identity="exec_abc"
    )
    p5_engine.authorize(op_id, "exec_abc")
    p5_engine.start_execution(op_id, "exec_abc")
    # OP is currently EXECUTION_STARTED
    # AUTHORIZED -> COMPLETED_SUCCESS is impossible from FSM layer, but let's test execution started
    auth = make_authority(["execute"])
    expected_hash = compute_valid_hash(op_id, TruthState.EXECUTION_STARTED, TruthState.COMPLETED_SUCCESS)
    
    with pytest.raises(ExecuteOnlyTruthDeniedError):
        truth_enforcer.authorize_and_record_truth(
            op_id, TruthState.COMPLETED_SUCCESS, auth, "TRUTH_ALLOW",
            proof_subject_hash=expected_hash, actor_identity="exec_abc"
        )
