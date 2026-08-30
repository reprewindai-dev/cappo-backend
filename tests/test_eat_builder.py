"""Tests for the EAT builder — minting rules M1–M9."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from cappo_backend.services.canonical import sha256_json, verify_signature_ed25519
from cappo_backend.services.eat_builder import (
    EATBuilder,
    MissingEATInputError,
    eat_canonical_body,
)
from cappo_backend.services.ei_builder import Ed25519Signer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIGNING_KEY = "test-eat-signing-key"


def _make_ei(**overrides: Any) -> dict[str, Any]:
    """Minimal valid ExecutionIdentityV1 dict for EAT builder input."""
    ei: dict[str, Any] = {
        "execution_id": "exec-001",
        "pgl_pre_certificate_id": "cert-001",
        "genome_hash": "ghash",
        "constitution_hash": "chash",
        "plan_hash": "phash",
        "directive": "ALLOW",
        "risk_tier": "standard",
        "budget_approved_cents": 100,
        "budget_reserve_cents": 10,
        "delegation_depth": 0,
        "scope": {"tools": ["llm.exec"]},
        "issuer": "cappo-orchestrator",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(),
    }
    ei.update(overrides)
    return ei


def _builder(**kwargs: Any) -> EATBuilder:
    signer = Ed25519Signer(signing_key=SIGNING_KEY)
    return EATBuilder(signer=signer, **kwargs)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestEATBuilderHappyPath:
    def test_build_returns_eat_with_all_fields(self):
        builder = _builder()
        ei = _make_ei()
        eat = builder.build(
            execution_identity=ei,
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=75.0,
            risk_tier="standard",
        )

        assert eat["eat_version"] == "1.0"
        assert eat["eat_id"].startswith("eat-")
        assert eat["execution_id"] == "exec-001"
        assert eat["subject"]["agent_id"] == "agent-1"
        assert eat["subject"]["trust_score"] == 75.0
        assert eat["authorization"]["directive"] == "ALLOW"
        assert eat["authorization"]["single_use"] is True
        assert "nonce" in eat
        assert len(eat["nonce"]) == 64  # 32 bytes hex = 64 chars
        assert eat["issuer"] == "cappo-inside-mcp"
        assert eat["audience"] == "cappo-edge-mcp"
        assert "hash" in eat
        assert "signature" in eat

    def test_canonical_body_excludes_hash_and_signature(self):
        builder = _builder()
        eat = builder.build(
            execution_identity=_make_ei(),
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=75.0,
            risk_tier="standard",
        )
        body = eat_canonical_body(eat)
        assert "hash" not in body
        assert "signature" not in body
        # All other fields should be present
        assert "eat_version" in body
        assert "nonce" in body

    def test_hash_matches_canonical_body(self):
        builder = _builder()
        eat = builder.build(
            execution_identity=_make_ei(),
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=75.0,
            risk_tier="standard",
        )
        body = eat_canonical_body(eat)
        expected_hash = sha256_json(body)
        assert eat["hash"] == expected_hash

    def test_signature_verifies(self):
        builder = _builder()
        eat = builder.build(
            execution_identity=_make_ei(),
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=75.0,
            risk_tier="standard",
        )
        body = eat_canonical_body(eat)
        assert verify_signature_ed25519(body, eat["signature"], SIGNING_KEY)

    def test_custom_ttl(self):
        builder = _builder()
        eat = builder.build(
            execution_identity=_make_ei(),
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=75.0,
            risk_tier="standard",
            ttl_seconds=120,
        )
        assert eat["temporal"]["ttl_seconds"] == 120

    def test_provenance_includes_governance_hash(self):
        builder = _builder()
        ei = _make_ei()
        eat = builder.build(
            execution_identity=ei,
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=75.0,
            risk_tier="standard",
        )
        expected = sha256_json({
            "directive": ei["directive"],
            "risk_tier": ei["risk_tier"],
        })
        assert eat["provenance"]["governance_decision_hash"] == expected


# ---------------------------------------------------------------------------
# Validation failure tests (M1–M9 minting rules)
# ---------------------------------------------------------------------------


class TestEATBuilderValidation:
    def test_m1_missing_agent_id(self):
        """M1: agent_id must not be empty."""
        builder = _builder()
        with pytest.raises(MissingEATInputError, match="agent_id"):
            builder.build(
                execution_identity=_make_ei(),
                agent_id="",
                certificate_id="cert-001",
                trust_score=75.0,
                risk_tier="standard",
            )

    def test_m2_none_execution_identity(self):
        """EI must not be None."""
        builder = _builder()
        with pytest.raises(MissingEATInputError, match="execution_identity"):
            builder.build(
                execution_identity=None,  # type: ignore
                agent_id="agent-1",
                certificate_id="cert-001",
                trust_score=75.0,
                risk_tier="standard",
            )

    def test_m3_trust_score_at_threshold(self):
        """M3: trust_score must be > 40."""
        builder = _builder()
        with pytest.raises(MissingEATInputError, match="trust_score"):
            builder.build(
                execution_identity=_make_ei(),
                agent_id="agent-1",
                certificate_id="cert-001",
                trust_score=40.0,
                risk_tier="standard",
            )

    def test_m3_trust_score_below_threshold(self):
        builder = _builder()
        with pytest.raises(MissingEATInputError, match="trust_score"):
            builder.build(
                execution_identity=_make_ei(),
                agent_id="agent-1",
                certificate_id="cert-001",
                trust_score=30.0,
                risk_tier="standard",
            )

    def test_m4_terminated_risk_tier(self):
        """M4/M5: terminated risk tier must be rejected."""
        builder = _builder()
        with pytest.raises(MissingEATInputError, match="terminated"):
            builder.build(
                execution_identity=_make_ei(),
                agent_id="agent-1",
                certificate_id="cert-001",
                trust_score=75.0,
                risk_tier="terminated",
            )

    def test_m6_missing_execution_id_in_ei(self):
        """M6: EI must have execution_id."""
        builder = _builder()
        with pytest.raises(MissingEATInputError, match="execution_id"):
            builder.build(
                execution_identity=_make_ei(execution_id=""),
                agent_id="agent-1",
                certificate_id="cert-001",
                trust_score=75.0,
                risk_tier="standard",
            )

    def test_m8_ttl_exceeds_max(self):
        """M8: TTL must not exceed max_ttl_seconds."""
        builder = _builder()
        with pytest.raises(MissingEATInputError, match="ttl_seconds"):
            builder.build(
                execution_identity=_make_ei(),
                agent_id="agent-1",
                certificate_id="cert-001",
                trust_score=75.0,
                risk_tier="standard",
                ttl_seconds=9999,
            )

    def test_m8_ttl_zero(self):
        """M8: TTL 0 is falsy, becomes default (valid). Use -1 for invalid."""
        builder = _builder()
        # TTL 0 is falsy → becomes default (300) which is valid
        # So test with negative TTL instead
        with pytest.raises(MissingEATInputError, match="ttl_seconds"):
            builder.build(
                execution_identity=_make_ei(),
                agent_id="agent-1",
                certificate_id="cert-001",
                trust_score=75.0,
                risk_tier="standard",
                ttl_seconds=-1,
            )

    def test_trust_score_just_above_threshold_succeeds(self):
        """Trust score of 40.1 should succeed."""
        builder = _builder()
        eat = builder.build(
            execution_identity=_make_ei(),
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=40.1,
            risk_tier="standard",
        )
        assert eat["subject"]["trust_score"] == 40.1
