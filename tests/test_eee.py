from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from cappo_backend.services.eee import EEEBuilder, EEEVerifier, VerificationVerdict


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=123000)


def _envelope_input(now: datetime) -> dict:
    started = now
    ended = now + timedelta(milliseconds=20)
    return {
        "eee_version": "0.1.0",
        "execution_id": "execution-1",
        "idempotency_key": "request-1",
        "issuer": "https://cappo.veklom.com",
        "enforcer": {"name": "cappo", "version": "0.1.0", "build_hash": "sha256:build"},
        "participant_identity": {"scheme": "veklom-machine", "identifier": "machine-1"},
        "principal": {"scheme": "veklom-tenant", "identifier": "tenant-1"},
        "capability_id": "llm.exec",
        "capability_hash": "sha256:capability",
        "capability_attenuation": {
            "resource_allowlist": [],
            "argument_constraints": {},
            "spend_ceiling": "0.00",
            "rate_limits": {},
            "delegation_depth": 0,
            "delegation_depth_max": 0,
        },
        "runtime_lineage": {
            "model": "test-model",
            "model_version": "1",
            "framework": "cappo",
            "framework_version": "0.1.0",
            "config_hash": "sha256:config",
        },
        "authority_chain": [
            {
                "type": "capability-grant",
                "artifact_hash": "sha256:grant",
                "issuer": "https://cappo.veklom.com",
                "granted_at": _timestamp(now - timedelta(seconds=1)),
                "expires_at": _timestamp(now + timedelta(minutes=1)),
            }
        ],
        "authority_window": {
            "not_before": _timestamp(now - timedelta(seconds=1)),
            "not_after": _timestamp(now + timedelta(minutes=1)),
        },
        "revocation_check": {"method": "status-list", "checked_at": _timestamp(now), "result": "valid"},
        "policy_bundle_id": "cappo-policy",
        "policy_hash": "sha256:policy",
        "policy_decisions": [
            {
                "gate": "authority",
                "rule_id": "authority.allow",
                "decision": "allow",
                "evaluated_at": _timestamp(now),
                "latency_ms": 1,
                "reason_code": "ALLOWED",
            }
        ],
        "enforcement_mode": "fail-closed",
        "input_commitment": "sha256:input",
        "allowed_effects": ["network:example.com:443"],
        "actual_effects": ["network:example.com:443"],
        "tool_actions": [],
        "budget": {
            "granted": {"tokens": 10, "cost_usd": "1.00", "wall_clock_ms": 50, "tool_calls": 1},
            "consumed": {"tokens": 5, "cost_usd": "0.50", "wall_clock_ms": 20, "tool_calls": 1},
        },
        "started_at": _timestamp(started),
        "ended_at": _timestamp(ended),
        "output_commitment": "sha256:output",
        "status": "completed",
        "violations": [],
        "validators": [],
        "timestamps": {"issued_at": _timestamp(ended)},
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_eee_builder_and_offline_verifier_accept_a_signed_envelope() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    envelope = builder.build(_envelope_input(_now()))

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)

    assert result.verdict is VerificationVerdict.VALID
    assert result.reasons == []


def test_eee_verifier_rejects_a_tampered_signed_member() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    envelope = builder.build(_envelope_input(_now()))
    envelope["budget"]["consumed"]["cost_usd"] = "0.75"

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)

    assert result.verdict is VerificationVerdict.INVALID
    assert "ENVELOPE_HASH_MISMATCH" in result.reasons


def test_eee_verifier_rejects_hash_algorithm_downgrade() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    envelope = builder.build(_envelope_input(_now()))
    envelope["hash_alg"] = "MD5"

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)

    assert result.verdict is VerificationVerdict.INVALID
    assert "HASH_ALGORITHM_NOT_ALLOWED" in result.reasons


def test_eee_verifier_keeps_post_issuance_attestations_detached() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    envelope = builder.build(_envelope_input(_now()))
    modified = deepcopy(envelope)
    modified["validators"].append("sha256:detached-attestation")

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(modified)

    assert result.verdict is VerificationVerdict.INVALID
    assert "ENVELOPE_HASH_MISMATCH" in result.reasons


def test_eee_verifier_rejects_an_effect_outside_the_grant() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    source = _envelope_input(_now())
    source["actual_effects"] = ["network:unapproved.example:443"]
    envelope = builder.build(source)

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)

    assert result.verdict is VerificationVerdict.INVALID
    assert "EFFECT_OUTSIDE_AUTHORITY" in result.reasons


def test_eee_verifier_rejects_budget_consumption_above_the_grant() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    source = _envelope_input(_now())
    source["budget"]["consumed"]["cost_usd"] = "1.01"
    envelope = builder.build(source)

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)

    assert result.verdict is VerificationVerdict.INVALID
    assert "BUDGET_EXCEEDED:cost_usd" in result.reasons


def test_eee_verifier_marks_unchecked_revocation_as_unresolved() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    source = _envelope_input(_now())
    source["revocation_check"] = {"method": "none", "checked_at": _timestamp(_now()), "result": "unknown"}
    envelope = builder.build(source)

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)

    assert result.verdict is VerificationVerdict.VALID_WITH_UNRESOLVED_REFS
    assert "REVOCATION_NOT_CHECKED" in result.reasons


def test_eee_verifier_rejects_execution_outside_authority_window() -> None:
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    now = _now()
    source = _envelope_input(now)
    source["authority_window"]["not_after"] = _timestamp(now - timedelta(seconds=1))
    envelope = builder.build(source)

    result = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)

    assert result.verdict is VerificationVerdict.INVALID
    assert "AUTHORITY_WINDOW_VIOLATION" in result.reasons
