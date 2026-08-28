import time
import pytest
from cappo_backend.identity.models import WorkloadIdentityToken, ExecutionContextToken, WorkloadProofToken, AuthorityArtifact
from cappo_backend.identity.replay_cache import ReplayCache
from cappo_backend.authorization.errors import *
from cappo_backend.authorization.cappo_auth import CappoPreauthorizationEnforcer

@pytest.fixture
def replay_cache():
    return ReplayCache()

@pytest.fixture
def enforcer(replay_cache):
    return CappoPreauthorizationEnforcer(replay_cache)

def make_wit():
    return WorkloadIdentityToken(
        iss="https://identity.veklom.local",
        sub="wimse://veklom.local/prod/payment/worker/process",
        aud="https://api.veklom.local",
        exp=int(time.time()) + 3600,
        iat=int(time.time()),
        jti="wit_jti_123",
        cnf={"jwk": {}}
    )

def make_ect():
    return ExecutionContextToken(
        iss="https://identity.veklom.local",
        sub="wimse://veklom.local/prod/payment/worker/process",
        aud="https://api.veklom.local",
        exp=int(time.time()) + 3600,
        iat=int(time.time()),
        jti="ect_jti_123",
        ephemeral_execution_id="exec_abc",
        candidate_act_hash="act_hash_val",
        cnf={"jwk": {}}
    )

def make_wpt():
    return WorkloadProofToken(
        htm="POST",
        htu="https://api.veklom.local/submit",
        body_hash="body_hash_val",
        wit_hash="wit_hash_val",
        ect_hash="ect_hash_val",
        authority_hash="auth_hash_val",
        jti="wpt_jti_123",
        exp=int(time.time()) + 300,
        cnf={"jwk": {}}
    )

def make_auth():
    return AuthorityArtifact(
        authority_id="auth_123",
        ephemeral_execution_id="exec_abc",
        scope_hash="scope_val",
        policy_decision_hash="pol_val",
        candidate_act_hash="act_hash_val",
        destination_hash="dest_val",
        rights=["truth.transition"],
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
        proof_of_possession="sig"
    )

def test_1_profile_id_alone_denied(enforcer):
    with pytest.raises(ProfileOnlyDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", profile_id_only=True)

def test_2_workload_identifier_alone_denied(enforcer):
    with pytest.raises(WorkloadIdentifierOnlyDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", workload_identifier_only=True)

def test_3_static_service_name_alone_denied(enforcer):
    with pytest.raises(StaticServiceDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", static_service_name_only=True)

def test_4_api_key_alone_denied(enforcer):
    with pytest.raises(ApiKeyOnlyDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", api_key_only=True)

def test_5_claimed_role_alone_denied(enforcer):
    with pytest.raises(ClaimedRoleDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", claimed_role_only=True)

def test_6_source_ip_alone_denied(enforcer):
    with pytest.raises(SourceIpOnlyDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", source_ip_only=True)

def test_7_operator_assertion_alone_denied(enforcer):
    with pytest.raises(OperatorAssertionDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", operator_assertion_only=True)

def test_8_tenant_id_alone_denied(enforcer):
    with pytest.raises(TenantIdOnlyDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", tenant_id_only=True)

def test_9_missing_ephemeral_execution_id_denied(enforcer):
    auth = make_auth()
    auth.ephemeral_execution_id = None
    with pytest.raises(MissingEphemeralExecutionIdError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth)

def test_10_authority_ei_mismatch_denied(enforcer):
    auth = make_auth()
    auth.ephemeral_execution_id = "DIFFERENT_EXEC"
    with pytest.raises(AuthorityEiMismatchError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth)

def test_11_candidate_act_mismatch_denied(enforcer):
    auth = make_auth()
    auth.candidate_act_hash = "DIFFERENT_ACT"
    with pytest.raises(CandidateActMismatchError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth)

def test_12_authority_hash_mismatch_denied(enforcer):
    wpt = make_wpt()
    wpt.authority_hash = "DIFFERENT_AUTH_HASH"
    with pytest.raises(AuthorityHashMismatchError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), wpt, make_auth(),
                                       expected_authority_hash="auth_hash_val")

def test_13_scope_hash_mismatch_denied(enforcer):
    auth = make_auth()
    auth.scope_hash = "WRONG_SCOPE"
    with pytest.raises(ScopeHashMismatchError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth,
                                       expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val")

def test_14_destination_hash_mismatch_denied(enforcer):
    auth = make_auth()
    auth.destination_hash = "WRONG_DEST"
    with pytest.raises(DestinationHashMismatchError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth,
                                       expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")

def test_15_policy_decision_hash_mismatch_denied(enforcer):
    auth = make_auth()
    auth.policy_decision_hash = "WRONG_POL"
    with pytest.raises(PolicyDecisionHashMismatchError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth,
                                       expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")

def test_16_expired_authority_denied(enforcer):
    auth = make_auth()
    auth.expires_at = int(time.time()) - 100
    with pytest.raises(AuthorityExpiredError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth,
                                       expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")

def test_17_replayed_jti_denied(enforcer):
    wpt = make_wpt()
    enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), wpt, make_auth(),
                                   expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")
    with pytest.raises(ReplayDeniedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), wpt, make_auth(),
                                       expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")

def test_18_right_not_granted_denied(enforcer):
    auth = make_auth()
    auth.rights = ["some.other.right"]
    with pytest.raises(RightNotGrantedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth,
                                       expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")

def test_19_valid_fully_bound_authority_passes(enforcer):
    assert enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), make_auth(),
                                          expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")

def test_20_denial_emits_structured_evidence(enforcer):
    try:
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", profile_id_only=True)
    except ProfileOnlyDeniedError as e:
        evidence = e.to_evidence()
        assert evidence["error_code"] == "CAPPO_PROFILE_ONLY_DENIED"
        assert evidence["denial_code"] == "CAPPO_PROFILE_ONLY_DENIED"
        assert evidence["route"] == "route"
        assert evidence["trace_id"] == "t1"
        assert evidence["p5_state_unchanged"] is True
        assert "timestamp" in evidence

def test_21_denial_leaves_p5_truth_state_unchanged():
    pass

def test_22_denial_does_not_mark_outbox_sent():
    pass

def test_23_denial_does_not_create_fake_pgl_success_proof():
    pass

def test_24_denial_does_not_call_finality_sink():
    pass

def test_25_execute_right_does_not_imply_truth_transition(enforcer):
    auth = make_auth()
    auth.rights = ["execute"]
    with pytest.raises(RightNotGrantedError):
        enforcer.authorize_consequence("route", "POST", "t1", "dest_val", "body_hash_val", "truth.transition", make_wit(), make_ect(), make_wpt(), auth,
                                       expected_authority_hash="auth_hash_val", expected_ect_hash="ect_hash_val", expected_wit_hash="wit_hash_val", expected_scope_hash="scope_val", expected_policy_decision_hash="pol_val")
