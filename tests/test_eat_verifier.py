"""Tests for the Edge Gateway EAT verifier — verification rules V1–V10."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from cappo_backend.security.edge_gateway import EATVerificationError, EdgeGateway
from cappo_backend.security.nonce_cache import InMemoryNonceCache
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json, sign_payload
from cappo_backend.services.eat_builder import EATBuilder, eat_canonical_body
from cappo_backend.services.ei_builder import Ed25519Signer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIGNING_KEY = "test-eat-signing-key"


def _make_ei(**overrides: Any) -> dict[str, Any]:
    """Minimal valid ExecutionIdentityV1 dict."""
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
        "scope": {"tools": ["llm.exec"]},
        "issuer": "cappo-orchestrator",
    }
    ei.update(overrides)
    return ei


def _mint_eat(**overrides: Any) -> dict[str, Any]:
    """Mint a valid EAT for testing."""
    signer = Ed25519Signer(signing_key=SIGNING_KEY)
    builder = EATBuilder(signer=signer)
    kwargs: dict[str, Any] = {
        "execution_identity": _make_ei(),
        "agent_id": "agent-1",
        "certificate_id": "cert-001",
        "trust_score": 75.0,
        "risk_tier": "standard",
    }
    kwargs.update(overrides)
    return builder.build(**kwargs)


def _make_gateway(db, nonce_cache=None, signing_key=None, audience=None):
    """Create an EdgeGateway with test dependencies."""
    audit = AuditService(db)
    return EdgeGateway(
        audit=audit,
        eat_signing_key=signing_key or SIGNING_KEY,
        nonce_cache=nonce_cache or InMemoryNonceCache(),
        audience=audience or "cappo-edge-mcp",
    )


# ---------------------------------------------------------------------------
# V1 — Signature verification
# ---------------------------------------------------------------------------


class TestV1Signature:
    def test_valid_signature_passes(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        gw.require_eat(eat)  # should not raise

    def test_bad_signature_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        eat["signature"] = "deadbeef"
        with pytest.raises(EATVerificationError, match="signature") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V1"

    def test_wrong_signing_key_rejected(self, db):
        gw = _make_gateway(db, signing_key="wrong-key")
        eat = _mint_eat()
        with pytest.raises(EATVerificationError, match="signature") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V1"


# ---------------------------------------------------------------------------
# V2 — Hash verification
# ---------------------------------------------------------------------------


class TestV2Hash:
    def test_tampered_hash_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        eat["hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
        # Re-sign so V1 passes but V2 still fails (hash != recomputed hash)
        # Actually, the signature is over the canonical body, not over the hash field.
        # So we need to tamper the hash without changing the body.
        # The hash field is excluded from signing, so we can just change it.
        with pytest.raises(EATVerificationError, match="hash") as exc_info:
            gw.require_eat(eat)
        # Will fail at V1 because signature was computed over original body
        # which includes the correct hash... wait, no. The hash is NOT in the
        # signed body. Let me re-check.
        # eat_canonical_body strips "hash" and "signature", so the body used
        # for signature verification is the same regardless of the hash field.
        # V1 signature check passes (body unchanged), then V2 hash check fails.
        assert exc_info.value.rule == "V2"


# ---------------------------------------------------------------------------
# V3 — Expiry
# ---------------------------------------------------------------------------


class TestV3Expiry:
    def test_expired_eat_rejected(self, db):
        """An EAT with expires_at in the past should be rejected."""
        signer = Ed25519Signer(signing_key=SIGNING_KEY)
        past = datetime.now(timezone.utc) - timedelta(seconds=10)

        # Build a manually-expired EAT
        builder = EATBuilder(
            signer=signer,
            _clock=lambda: past - timedelta(seconds=300),  # issued 310s ago
        )
        eat = builder.build(
            execution_identity=_make_ei(),
            agent_id="agent-1",
            certificate_id="cert-001",
            trust_score=75.0,
            risk_tier="standard",
            ttl_seconds=300,  # expired 10s ago
        )

        gw = _make_gateway(db)
        with pytest.raises(EATVerificationError, match="expired") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V3"

    def test_missing_expires_at_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        eat["temporal"]["expires_at"] = None
        # Re-sign the tampered body
        body = eat_canonical_body(eat)
        eat["hash"] = sha256_json(body)
        eat["signature"] = sign_payload(body, SIGNING_KEY)

        with pytest.raises(EATVerificationError, match="expires_at") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V3"


# ---------------------------------------------------------------------------
# V4 — Nonce (replay protection)
# ---------------------------------------------------------------------------


class TestV4Nonce:
    def test_replay_detected(self, db):
        """Second presentation of the same nonce should be rejected."""
        cache = InMemoryNonceCache()
        gw = _make_gateway(db, nonce_cache=cache)
        eat = _mint_eat()

        gw.require_eat(eat)  # first time — passes

        with pytest.raises(EATVerificationError, match="replay") as exc_info:
            gw.require_eat(eat)  # replay — rejected
        assert exc_info.value.rule == "V4"

    def test_missing_nonce_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        eat["nonce"] = ""
        body = eat_canonical_body(eat)
        eat["hash"] = sha256_json(body)
        eat["signature"] = sign_payload(body, SIGNING_KEY)

        with pytest.raises(EATVerificationError, match="nonce") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V4"


# ---------------------------------------------------------------------------
# V5 — Directive
# ---------------------------------------------------------------------------


class TestV5Directive:
    def test_deny_directive_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        eat["authorization"]["directive"] = "DENY"
        body = eat_canonical_body(eat)
        eat["hash"] = sha256_json(body)
        eat["signature"] = sign_payload(body, SIGNING_KEY)

        with pytest.raises(EATVerificationError, match="directive") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V5"

    def test_allow_with_audit_accepted(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat(execution_identity=_make_ei(directive="ALLOW_WITH_AUDIT"))
        gw.require_eat(eat)  # should not raise


# ---------------------------------------------------------------------------
# V6 — Scope
# ---------------------------------------------------------------------------


class TestV6Scope:
    def test_out_of_scope_action_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        with pytest.raises(EATVerificationError, match="scope") as exc_info:
            gw.require_eat(eat, action="file.delete")
        assert exc_info.value.rule == "V6"

    def test_in_scope_action_accepted(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        gw.require_eat(eat, action="llm.exec")  # should not raise

    def test_no_action_skips_scope_check(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        gw.require_eat(eat, action="")  # should not raise


# ---------------------------------------------------------------------------
# V7 — Budget
# ---------------------------------------------------------------------------


class TestV7Budget:
    def test_insufficient_budget_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()  # budget = 100 cents
        with pytest.raises(EATVerificationError, match="budget") as exc_info:
            gw.require_eat(eat, action_cost_cents=999)
        assert exc_info.value.rule == "V7"

    def test_sufficient_budget_accepted(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()  # budget = 100 cents
        gw.require_eat(eat, action_cost_cents=50)  # should not raise

    def test_zero_cost_skips_check(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        gw.require_eat(eat, action_cost_cents=0)  # should not raise


# ---------------------------------------------------------------------------
# V9 — Version
# ---------------------------------------------------------------------------


class TestV9Version:
    def test_unknown_version_rejected(self, db):
        gw = _make_gateway(db)
        eat = _mint_eat()
        eat["eat_version"] = "2.0"
        body = eat_canonical_body(eat)
        eat["hash"] = sha256_json(body)
        eat["signature"] = sign_payload(body, SIGNING_KEY)

        with pytest.raises(EATVerificationError, match="eat_version") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V9"


# ---------------------------------------------------------------------------
# V10 — Audience
# ---------------------------------------------------------------------------


class TestV10Audience:
    def test_wrong_audience_rejected(self, db):
        gw = _make_gateway(db, audience="other-edge")
        eat = _mint_eat()  # audience = "cappo-edge-mcp"
        with pytest.raises(EATVerificationError, match="audience") as exc_info:
            gw.require_eat(eat)
        assert exc_info.value.rule == "V10"

    def test_correct_audience_accepted(self, db):
        gw = _make_gateway(db, audience="cappo-edge-mcp")
        eat = _mint_eat()
        gw.require_eat(eat)  # should not raise


# ---------------------------------------------------------------------------
# V0 — Missing EAT
# ---------------------------------------------------------------------------


class TestV0MissingEAT:
    def test_none_eat_rejected(self, db):
        gw = _make_gateway(db)
        with pytest.raises(EATVerificationError, match="missing") as exc_info:
            gw.require_eat(None)
        assert exc_info.value.rule == "V0"
