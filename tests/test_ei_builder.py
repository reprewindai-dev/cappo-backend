"""Tests for the canonical ExecutionIdentityV1 builder (Task 2)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.ei_builder import (
    Ed25519Signer,
    ExecutionIdentityBuilder,
    MissingEIInputError,
    canonical_body,
)

SIGNING_KEY = "test-signing-key"


def _min_inputs(**overrides: object) -> dict:
    base = {
        "pgl_pre_certificate_id": "cert-1",
        "genome_hash": "g-hash",
        "constitution_hash": "c-hash",
        "plan_hash": "p-hash",
        "directive": "ALLOW",
        "risk_tier": "standard",
        "scope": {"tools": ["llm.exec"]},
        "issuer": "test-orch",
        "execution_id": "ei-fixed",
        "issued_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


@pytest.fixture
def builder() -> ExecutionIdentityBuilder:
    return ExecutionIdentityBuilder(signer=Ed25519Signer(SIGNING_KEY))


class TestDeterminism:
    def test_same_inputs_same_output(self, builder: ExecutionIdentityBuilder) -> None:
        a = builder.build(_min_inputs())
        b = builder.build(_min_inputs())
        assert a == b

    def test_different_inputs_different_output(self, builder: ExecutionIdentityBuilder) -> None:
        a = builder.build(_min_inputs())
        b = builder.build(_min_inputs(plan_hash="different"))
        assert a["hash"] != b["hash"]
        assert a["signature"] != b["signature"]


class TestHashAndSignature:
    def test_hash_matches_body(self, builder: ExecutionIdentityBuilder) -> None:
        ei = builder.build(_min_inputs())
        assert ei["hash"] == sha256_json(canonical_body(ei))

    def test_signature_verifies(self, builder: ExecutionIdentityBuilder) -> None:
        ei = builder.build(_min_inputs())
        signer = Ed25519Signer(SIGNING_KEY)
        assert signer.verify(canonical_body(ei), ei["signature"])

    def test_wrong_key_fails_verify(self, builder: ExecutionIdentityBuilder) -> None:
        ei = builder.build(_min_inputs())
        wrong = Ed25519Signer("wrong-key")
        assert not wrong.verify(canonical_body(ei), ei["signature"])


class TestMissingInputs:
    @pytest.mark.parametrize("field", [
        "pgl_pre_certificate_id",
        "genome_hash",
        "constitution_hash",
        "plan_hash",
        "directive",
        "risk_tier",
        "scope",
        "issuer",
    ])
    def test_missing_required_field_raises(self, builder: ExecutionIdentityBuilder, field: str) -> None:
        inputs = _min_inputs()
        del inputs[field]
        with pytest.raises(MissingEIInputError, match=field):
            builder.build(inputs)


class TestFieldPopulation:
    def test_all_ei_fields_present(self, builder: ExecutionIdentityBuilder) -> None:
        ei = builder.build(_min_inputs())
        for field in ("execution_id", "pgl_pre_certificate_id", "genome_hash",
                       "constitution_hash", "plan_hash", "directive", "risk_tier",
                       "scope", "issuer", "issued_at", "expires_at", "ttl_seconds",
                       "hash", "signature"):
            assert field in ei, f"missing field: {field}"

    def test_custom_ttl(self, builder: ExecutionIdentityBuilder) -> None:
        ei = builder.build(_min_inputs(ttl_seconds=600))
        assert ei["ttl_seconds"] == 600
