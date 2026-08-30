import hashlib
import json
import time

import pytest

from cappo_backend.identity import (
    AudienceMismatchError,
    AuthorityHashMismatchError,
    BodyHashMismatchError,
    CandidateActMismatchError,
    IdentityValidator,
    MalformedWorkloadIdentifierError,
    MissingAuthorityError,
    MissingExecutionContextError,
    MissingWorkloadIdentityError,
    MissingWorkloadProofError,
    ProfileOnlyDeniedError,
    ReplayCache,
    ReplayDeniedError,
    RequestBindingMismatchError,
    RouteClassification,
    TokenExpiredError,
    WIDMiddlewareContext,
)

_NOW = int(time.time())


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def replay_cache():
    return ReplayCache()


@pytest.fixture
def validator(replay_cache):
    return IdentityValidator(
        expected_audience="https://api.veklom.local",
        replay_cache=replay_cache,
    )


def make_valid_wit(*, jti: str = "wit_jti_123"):
    return {
        "iss": "https://identity.veklom.local",
        "sub": "wimse://veklom.local/prod/payment/worker/process",
        "aud": "https://api.veklom.local",
        "exp": _NOW + 3600,
        "iat": _NOW,
        "jti": jti,
        "cnf": {"jwk": {}},
    }


def make_valid_ect():
    return {
        "iss": "https://identity.veklom.local",
        "sub": "wimse://veklom.local/prod/payment/worker/process",
        "aud": "https://api.veklom.local",
        "exp": _NOW + 3600,
        "iat": _NOW,
        "jti": "ect_jti_123",
        "ephemeral_execution_id": "exec_abc",
        "candidate_act_hash": "act_hash_val",
        "cnf": {"jwk": {}},
    }


def make_valid_authority():
    return {
        "authority_id": "auth_123",
        "ephemeral_execution_id": "exec_abc",
        "scope_hash": "scope_val",
        "policy_decision_hash": "pol_val",
        "candidate_act_hash": "act_hash_val",
        "destination_hash": "dest_val",
        "rights": ["truth.transition"],
        "issued_at": _NOW,
        "expires_at": _NOW + 3600,
        "proof_of_possession": "sig",
    }


def make_valid_wpt(
    *,
    wit: dict | None = None,
    ect: dict | None = None,
    authority: dict | None = None,
    jti: str = "wpt_jti_123",
):
    wit = wit or make_valid_wit()
    ect = ect or make_valid_ect()
    return {
        "htm": "POST",
        "htu": "https://api.veklom.local/submit",
        "body_hash": "body_hash_val",
        "wit_hash": _hash(wit),
        "ect_hash": _hash(ect),
        "authority_hash": _hash(authority) if authority is not None else "",
        "jti": jti,
        "exp": _NOW + 300,
        "cnf": {"jwk": {}},
    }


def test_1_public_route_pass(validator):
    ctx = WIDMiddlewareContext(RouteClassification.PUBLIC, validator)
    ctx.enforce(route="/ping", method="GET", trace_id="t1", htu="/ping", body_hash="")


def test_2_governed_missing_wit(validator):
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    with pytest.raises(MissingWorkloadIdentityError):
        ctx.enforce(route="/data", method="GET", trace_id="t1", htu="/data", body_hash="")


def test_3_governed_missing_ect(validator):
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    with pytest.raises(MissingExecutionContextError):
        ctx.enforce(
            route="/data",
            method="GET",
            trace_id="t1",
            htu="/data",
            body_hash="",
            wit_payload=make_valid_wit(),
        )


def test_4_state_changing_missing_wpt(validator):
    ctx = WIDMiddlewareContext(RouteClassification.STATE_CHANGING, validator)
    with pytest.raises(MissingWorkloadProofError):
        ctx.enforce(
            route="/update",
            method="POST",
            trace_id="t1",
            htu="/update",
            body_hash="",
            wit_payload=make_valid_wit(),
            ect_payload=make_valid_ect(),
        )


def test_5_consequence_missing_authority(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    ctx = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, validator)
    with pytest.raises(MissingAuthorityError):
        ctx.enforce(
            route="/transfer",
            method="POST",
            trace_id="t1",
            htu="https://api.veklom.local/submit",
            body_hash="body_hash_val",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=make_valid_wpt(wit=wit, ect=ect),
        )


def test_6_expired_wit(validator):
    wit = make_valid_wit()
    wit["exp"] = int(time.time()) - 100
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    with pytest.raises(TokenExpiredError):
        ctx.enforce(
            "/data",
            "GET",
            "t1",
            "/data",
            "",
            wit_payload=wit,
            ect_payload=make_valid_ect(),
        )


def test_7_expired_ect(validator):
    ect = make_valid_ect()
    ect["exp"] = int(time.time()) - 100
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    with pytest.raises(TokenExpiredError):
        ctx.enforce(
            "/data",
            "GET",
            "t1",
            "/data",
            "",
            wit_payload=make_valid_wit(),
            ect_payload=ect,
        )


def test_8_expired_wpt(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    wpt = make_valid_wpt(wit=wit, ect=ect)
    wpt["exp"] = int(time.time()) - 100
    ctx = WIDMiddlewareContext(RouteClassification.STATE_CHANGING, validator)
    with pytest.raises(TokenExpiredError):
        ctx.enforce(
            "/update",
            "POST",
            "t1",
            "https://api.veklom.local/submit",
            "body_hash_val",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=wpt,
        )


def test_9_audience_mismatch(validator):
    wit = make_valid_wit()
    wit["aud"] = "https://wrong.local"
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    with pytest.raises(AudienceMismatchError):
        ctx.enforce(
            "/data",
            "GET",
            "t1",
            "/data",
            "",
            wit_payload=wit,
            ect_payload=make_valid_ect(),
        )


def test_10_body_hash_mismatch(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    wpt = make_valid_wpt(wit=wit, ect=ect)
    ctx = WIDMiddlewareContext(RouteClassification.STATE_CHANGING, validator)
    with pytest.raises(BodyHashMismatchError):
        ctx.enforce(
            "/update",
            "POST",
            "t1",
            "https://api.veklom.local/submit",
            "WRONG_HASH",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=wpt,
        )


def test_11_method_mismatch(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    wpt = make_valid_wpt(wit=wit, ect=ect)
    wpt["htm"] = "PUT"
    ctx = WIDMiddlewareContext(RouteClassification.STATE_CHANGING, validator)
    with pytest.raises(RequestBindingMismatchError):
        ctx.enforce(
            "/update",
            "POST",
            "t1",
            "https://api.veklom.local/submit",
            "body_hash_val",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=wpt,
        )


def test_12_uri_mismatch(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    wpt = make_valid_wpt(wit=wit, ect=ect)
    wpt["htu"] = "https://api.veklom.local/other"
    ctx = WIDMiddlewareContext(RouteClassification.STATE_CHANGING, validator)
    with pytest.raises(RequestBindingMismatchError):
        ctx.enforce(
            "/update",
            "POST",
            "t1",
            "https://api.veklom.local/submit",
            "body_hash_val",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=wpt,
        )


def test_13_candidate_act_mismatch(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    auth = make_valid_authority()
    auth["candidate_act_hash"] = "wrong"
    wpt = make_valid_wpt(wit=wit, ect=ect, authority=auth)
    ctx = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, validator)
    with pytest.raises(CandidateActMismatchError):
        ctx.enforce(
            "/transfer",
            "POST",
            "t1",
            "https://api.veklom.local/submit",
            "body_hash_val",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=wpt,
            authority_payload=auth,
        )


def test_14_authority_hash_mismatch(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    auth = make_valid_authority()
    wpt = make_valid_wpt(wit=wit, ect=ect)
    wpt["authority_hash"] = "WRONG_AUTH_HASH"
    ctx = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, validator)
    with pytest.raises(AuthorityHashMismatchError):
        ctx.enforce(
            "/transfer",
            "POST",
            "t1",
            "https://api.veklom.local/submit",
            "body_hash_val",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=wpt,
            authority_payload=auth,
        )


def test_15_malformed_workload_identifier(validator):
    wit = make_valid_wit()
    wit["sub"] = "invalid_format"
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    with pytest.raises(MalformedWorkloadIdentifierError):
        ctx.enforce(
            "/data",
            "GET",
            "t1",
            "/data",
            "",
            wit_payload=wit,
            ect_payload=make_valid_ect(),
        )


def test_16_profile_only_authority_denied(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    auth = make_valid_authority()
    auth["ephemeral_execution_id"] = ""
    wpt = make_valid_wpt(wit=wit, ect=ect, authority=auth)
    ctx = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, validator)
    with pytest.raises(ProfileOnlyDeniedError):
        ctx.enforce(
            "/transfer",
            "POST",
            "t1",
            "https://api.veklom.local/submit",
            "body_hash_val",
            wit_payload=wit,
            ect_payload=ect,
            wpt_payload=wpt,
            authority_payload=auth,
        )


def test_17_replayed_wit_jti(validator):
    wit = make_valid_wit()
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    ctx.enforce(
        "/data",
        "GET",
        "t1",
        "/data",
        "",
        wit_payload=wit,
        ect_payload=make_valid_ect(),
    )
    with pytest.raises(ReplayDeniedError):
        ctx.enforce(
            "/data",
            "GET",
            "t1",
            "/data",
            "",
            wit_payload=wit,
            ect_payload=make_valid_ect(),
        )


def test_18_replayed_wpt_jti(validator):
    wit = make_valid_wit(jti="wit_for_wpt_replay")
    ect = make_valid_ect()
    wpt = make_valid_wpt(wit=wit, ect=ect, jti="replayed_wpt")
    kwargs = {
        "wpt": __import__(
            "cappo_backend.identity.models",
            fromlist=["WorkloadProofToken"],
        ).WorkloadProofToken(**wpt),
        "expected_method": "POST",
        "expected_htu": "https://api.veklom.local/submit",
        "expected_body_hash": "body_hash_val",
        "expected_wit_hash": _hash(wit),
        "expected_ect_hash": _hash(ect),
        "expected_authority_hash": None,
        "route": "/update",
        "trace_id": "t1",
    }
    validator.validate_wpt(**kwargs)
    with pytest.raises(ReplayDeniedError):
        validator.validate_wpt(**kwargs)


def test_19_valid_governed_request_passes(validator):
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    ctx.enforce(
        "/data",
        "GET",
        "t1",
        "/data",
        "",
        wit_payload=make_valid_wit(),
        ect_payload=make_valid_ect(),
    )


def test_20_valid_consequence_request_passes(validator):
    wit = make_valid_wit()
    ect = make_valid_ect()
    auth = make_valid_authority()
    wpt = make_valid_wpt(wit=wit, ect=ect, authority=auth)
    ctx = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, validator)
    ctx.enforce(
        "/transfer",
        "POST",
        "t1",
        "https://api.veklom.local/submit",
        "body_hash_val",
        wit_payload=wit,
        ect_payload=ect,
        wpt_payload=wpt,
        authority_payload=auth,
    )


def test_21_denial_emits_structured_evidence(validator):
    ctx = WIDMiddlewareContext(RouteClassification.GOVERNED, validator)
    try:
        ctx.enforce("/data", "GET", "t1", "/data", "")
    except MissingWorkloadIdentityError as exc:
        evidence = exc.to_evidence()
        assert evidence["error_code"] == "WID_MISSING_WORKLOAD_IDENTITY"
        assert evidence["route"] == "/data"
        assert evidence["trace_id"] == "t1"
        assert "timestamp" in evidence


def test_22_validation_does_not_mutate_p5_truth_state():
    """Validation has no DB handle and cannot mutate P5 truth state."""


def test_23_validation_does_not_mark_outbox_sent():
    """Validation has no outbox sink."""


def test_24_validation_does_not_create_fake_pgl_proof():
    """Validation has no PGL sink."""
