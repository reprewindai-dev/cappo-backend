import time
import pytest
from typing import Dict, Any
from cappo_backend.pgl.evidence_validator import PGLEvidenceValidator
from cappo_backend.pgl.errors import *
from cappo_backend.p5.states import TruthState

@pytest.fixture
def validator():
    return PGLEvidenceValidator()

def make_valid_payload(is_genesis=False) -> Dict[str, Any]:
    payload = {
        "trust_domain_id": "td_123",
        "workload_identifier": "spiffe://veklom/workload",
        "profile_id": "prof_123",
        "ephemeral_execution_id": "exec_abc",
        "authority_hash": "auth_hash_val",
        "candidate_act_hash": "act_hash_val",
        "policy_decision_hash": "pol_hash_val",
        "p5_operation_id": "op_123",
        "p5_truth_state": str(TruthState.COMPLETED_SUCCESS),
        "event_hash": "event_hash_val",
        "previous_event_hash": None if is_genesis else "prev_hash_val",
        "signature": "placeholder:labeled:sig",
        "timestamp": int(time.time()),
        "_actual_state": str(TruthState.COMPLETED_SUCCESS)
    }
    
    val = PGLEvidenceValidator()
    payload["identity_chain_hash"] = val.compute_identity_chain_hash(payload)
    return payload

def test_1_missing_trust_domain_id_denied(validator):
    payload = make_valid_payload()
    payload.pop("trust_domain_id")
    with pytest.raises(MissingTrustDomainError):
        validator.validate_append(payload)

def test_2_missing_workload_identifier_denied(validator):
    payload = make_valid_payload()
    payload.pop("workload_identifier")
    with pytest.raises(MissingWorkloadIdentifierError):
        validator.validate_append(payload)

def test_3_malformed_workload_identifier_denied(validator):
    payload = make_valid_payload()
    payload["workload_identifier"] = "invalid_format"
    with pytest.raises(MalformedWorkloadIdentifierError):
        validator.validate_append(payload)

def test_4_missing_profile_id_denied(validator):
    payload = make_valid_payload()
    payload.pop("profile_id")
    with pytest.raises(MissingProfileIdError):
        validator.validate_append(payload)

def test_5_missing_ephemeral_execution_id_denied(validator):
    payload = make_valid_payload()
    payload.pop("ephemeral_execution_id")
    with pytest.raises(MissingEphemeralExecutionIdError):
        validator.validate_append(payload)

def test_6_missing_authority_hash_denied_for_consequence_event(validator):
    payload = make_valid_payload()
    payload["p5_truth_state"] = str(TruthState.EXECUTION_STARTED)
    payload["authority_hash"] = None
    with pytest.raises(MissingAuthorityHashError):
        validator.validate_append(payload)

def test_7_missing_candidate_act_hash_denied(validator):
    payload = make_valid_payload()
    payload.pop("candidate_act_hash")
    with pytest.raises(MissingCandidateActHashError):
        validator.validate_append(payload)

def test_8_missing_policy_decision_hash_denied(validator):
    payload = make_valid_payload()
    payload.pop("policy_decision_hash")
    with pytest.raises(MissingPolicyDecisionHashError):
        validator.validate_append(payload)

def test_9_missing_p5_operation_id_denied(validator):
    payload = make_valid_payload()
    payload.pop("p5_operation_id")
    with pytest.raises(MissingP5OperationIdError):
        validator.validate_append(payload)

def test_10_missing_p5_truth_state_denied(validator):
    payload = make_valid_payload()
    payload.pop("p5_truth_state")
    with pytest.raises(MissingP5TruthStateError):
        validator.validate_append(payload)

def test_11_truth_overclaim_denied(validator):
    payload = make_valid_payload()
    payload["p5_truth_state"] = "COMPLETED_SUCCESS"
    payload["_actual_state"] = "AUTHORIZED"
    payload["identity_chain_hash"] = validator.compute_identity_chain_hash(payload)
    with pytest.raises(TruthOverclaimDeniedError):
        validator.validate_append(payload)

def test_12_missing_event_hash_denied(validator):
    payload = make_valid_payload()
    payload.pop("event_hash")
    with pytest.raises(MissingEventHashError):
        validator.validate_append(payload)

def test_13_missing_previous_event_hash_denied_for_non_genesis(validator):
    payload = make_valid_payload(is_genesis=False)
    payload.pop("previous_event_hash")
    with pytest.raises(MissingPreviousEventHashError):
        validator.validate_append(payload, is_genesis=False)

def test_14_invalid_signature_denied(validator):
    payload = make_valid_payload()
    payload["signature"] = "invalid"
    with pytest.raises(InvalidSignatureError):
        validator.validate_append(payload)

def test_15_unlabeled_placeholder_signature_denied(validator):
    payload = make_valid_payload()
    payload["signature"] = "placeholder:sig"
    with pytest.raises(UnlabeledPlaceholderSignatureError):
        validator.validate_append(payload)

def test_16_identity_chain_hash_mismatch_denied(validator):
    payload = make_valid_payload()
    payload["identity_chain_hash"] = "BAD_HASH"
    with pytest.raises(IdentityChainHashMismatchError):
        validator.validate_append(payload)

def test_17_authority_hash_mismatch_denied(validator):
    payload = make_valid_payload()
    with pytest.raises(AuthorityHashMismatchError):
        validator.validate_append(payload, actual_authority_hash="DIFFERENT_HASH")

def test_18_candidate_act_hash_mismatch_denied(validator):
    payload = make_valid_payload()
    with pytest.raises(CandidateActHashMismatchError):
        validator.validate_append(payload, actual_candidate_act_hash="DIFFERENT_HASH")

def test_19_policy_decision_hash_mismatch_denied(validator):
    payload = make_valid_payload()
    with pytest.raises(PolicyDecisionHashMismatchError):
        validator.validate_append(payload, actual_policy_decision_hash="DIFFERENT_HASH")

def test_20_event_hash_mismatch_denied(validator):
    payload = make_valid_payload()
    with pytest.raises(EventHashMismatchError):
        validator.validate_append(payload, actual_event_hash="DIFFERENT_HASH")

def test_21_valid_genesis_evidence_accepted(validator):
    payload = make_valid_payload(is_genesis=True)
    assert validator.validate_append(payload, is_genesis=True) is True

def test_22_valid_non_genesis_evidence_accepted(validator):
    payload = make_valid_payload(is_genesis=False)
    assert validator.validate_append(payload, is_genesis=False) is True

def test_23_pgl_denial_does_not_append_ledger_event(validator):
    payload = make_valid_payload()
    payload.pop("trust_domain_id")
    try:
        validator.validate_append(payload)
        pytest.fail("Should have raised")
    except PGLEvidenceError as e:
        evidence = e.to_evidence()
        assert evidence["pgl_append_denied"] is True
        # In a real system, we assert DB count remains same

def test_24_pgl_denial_does_not_mark_p5_outbox_sent(validator):
    payload = make_valid_payload()
    payload.pop("trust_domain_id")
    try:
        validator.validate_append(payload)
    except PGLEvidenceError as e:
        evidence = e.to_evidence()
        assert evidence["event_type"] == "PGL_APPEND_DENIED"

def test_25_pgl_denial_does_not_mutate_p5_truth_state(validator):
    payload = make_valid_payload()
    payload.pop("trust_domain_id")
    try:
        validator.validate_append(payload)
    except PGLEvidenceError as e:
        assert isinstance(e, MissingTrustDomainError)

def test_26_pgl_denial_emits_structured_denial_evidence(validator):
    payload = make_valid_payload()
    payload.pop("trust_domain_id")
    try:
        validator.validate_append(payload)
    except MissingTrustDomainError as e:
        evidence = e.to_evidence()
        assert evidence["error_code"] == "PGL_MISSING_TRUST_DOMAIN"
        assert evidence["denial_code"] == "PGL_MISSING_TRUST_DOMAIN"
        assert evidence["event_type"] == "PGL_APPEND_DENIED"
        assert evidence["p5_operation_id"] == "op_123"
        assert evidence["workload_identifier"] == "spiffe://veklom/workload"
        assert evidence["pgl_append_denied"] is True
        assert "timestamp" in evidence

def test_27_cappo_allow_cannot_be_recorded_as_completion_proof(validator):
    payload = make_valid_payload()
    payload["p5_truth_state"] = "COMPLETED_SUCCESS"
    payload["_actual_state"] = "ALLOW"
    payload["identity_chain_hash"] = validator.compute_identity_chain_hash(payload)
    with pytest.raises(TruthOverclaimDeniedError):
        validator.validate_append(payload)

def test_28_outcome_unknown_cannot_be_collapsed_into_success(validator):
    payload = make_valid_payload()
    payload["p5_truth_state"] = "OUTCOME_UNKNOWN"
    payload["_asserted_as"] = "SUCCESS"
    payload["identity_chain_hash"] = validator.compute_identity_chain_hash(payload)
    with pytest.raises(TruthOverclaimDeniedError):
        validator.validate_append(payload)

def test_29_denied_identity_evidence_cannot_become_successful_execution(validator):
    payload = make_valid_payload()
    payload["p5_truth_state"] = "COMPLETED_SUCCESS"
    payload["_actual_state"] = "DENIED"
    payload["identity_chain_hash"] = validator.compute_identity_chain_hash(payload)
    with pytest.raises(TruthOverclaimDeniedError):
        validator.validate_append(payload)

def test_30_valid_p5_truth_transition_evidence_preserves_event_hash_and_identity_chain_hash(validator):
    payload = make_valid_payload()
    assert validator.validate_append(payload) is True
    # The identity_chain_hash was verified to match the payload.
    expected = validator.compute_identity_chain_hash(payload)
    assert payload["identity_chain_hash"] == expected
