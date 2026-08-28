"""Adversarial tests for P0-9 (Production config fail-closed cleanup)."""

from __future__ import annotations

import pytest

from cappo_backend.config import InsecureProductionConfigError, Settings


def test_compromised_key_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENV", "production")
    
    # We attempt to initialize Settings with the compromised key in production
    # It must raise InsecureProductionConfigError immediately
    with pytest.raises(InsecureProductionConfigError, match="Compromised CAPPO API credential detected"):
        Settings(
            _env_file=None,
            environment="production",
            api_keys="key1,cappo_internal_exec_key_veklom_2026,key3",
        )

def test_compromised_key_warned_and_replaced_in_development(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    
    # In development, it must warn and replace the key silently
    settings = Settings(
        _env_file=None,
        environment="development",
        api_keys="key1,cappo_internal_exec_key_veklom_2026,key3",
    )
    
    api_keys = list(settings.api_key_set)
    assert len(api_keys) == 3
    assert "key1" in api_keys
    assert "key3" in api_keys
    # The compromised literal must not be in the keys
    assert "cappo_internal_exec_key_veklom_2026" not in api_keys
    
    # Verify that the replaced key is a random token (e.g. 64 hex characters)
    replaced_key = [k for k in api_keys if k != "key1" and k != "key3"][0]
    assert len(replaced_key) == 64  # secrets.token_hex(32) is 64 hex chars
