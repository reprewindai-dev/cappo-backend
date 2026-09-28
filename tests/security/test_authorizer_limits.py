from __future__ import annotations

from datetime import timedelta

import pytest
from biscuit_auth import AuthorizerBuilder, KeyPair

import cappo_backend.security.biscuit as biscuit
from cappo_backend.config import Settings


@pytest.fixture(autouse=True)
def _root_key(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = KeyPair()
    settings = Settings(
        environment="test",
        biscuit_root_private_key_hex=configured.private_key.to_bytes().hex(),
    )
    monkeypatch.setattr(biscuit, "get_settings", lambda: settings)
    biscuit._ROOT_KEY_PAIR = None
    yield
    biscuit._ROOT_KEY_PAIR = None


def _mint() -> str:
    return biscuit.mint_biscuit_capability(
        caller_spiffe_id="spiffe://example.org/caller",
        executor_spiffe_id="spiffe://example.org/executor",
        capability_id="governed-counter@v1",
        reads=["counter.read"],
        writes=["counter.increment"],
        execution_id="execution-limits",
        ttl_seconds=60,
    )


def test_apply_authorizer_limits_replaces_library_defaults() -> None:
    builder = AuthorizerBuilder()
    before = builder.limits()
    assert before.max_time == timedelta(milliseconds=1)
    assert before.max_iterations == 100

    biscuit.apply_authorizer_limits(builder)
    after = builder.limits()
    assert after.max_time == biscuit.AUTHORIZER_MAX_TIME
    assert after.max_iterations == biscuit.AUTHORIZER_MAX_ITERATIONS
    assert after.max_facts == biscuit.AUTHORIZER_MAX_FACTS


def test_extract_authority_context_repeatedly_under_configured_budget() -> None:
    token = _mint()
    for _ in range(200):
        authority = biscuit.extract_authority_context(token)
        assert authority is not None
        assert authority.allowed_actions == {"counter.read", "counter.increment"}


def _verify(token: str, action: str) -> bool:
    trusted = biscuit.TrustedRevocationState()
    trusted.known_epochs["workspace"] = 0
    return biscuit.verify_biscuit_capability(
        token,
        executor_spiffe_id="spiffe://example.org/executor",
        action=action,
        resource="counter-1",
        subject_spiffe_id="spiffe://example.org/caller",
        trusted_state=trusted,
        execution_id="execution-limits",
    )


def test_verify_biscuit_capability_allows_in_scope_and_denies_out_of_scope() -> None:
    token = _mint()
    assert _verify(token, "counter.increment")
    assert not _verify(token, "counter.reset")


def test_exhausted_budget_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _mint()
    monkeypatch.setattr(biscuit, "AUTHORIZER_MAX_ITERATIONS", 0)
    monkeypatch.setattr(biscuit, "AUTHORIZER_MAX_FACTS", 0)
    monkeypatch.setattr(biscuit, "AUTHORIZER_MAX_TIME", timedelta(0))

    assert biscuit.extract_authority_context(token) is None
    assert not _verify(token, "counter.increment")
