"""Adversarial tests for P0-9 (Production config fail-closed cleanup)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cappo_backend.config import Settings


def test_compromised_key_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENV", "production")

    # We attempt to initialize Settings with the compromised key in production
    # It must be rejected immediately
    with pytest.raises(ValidationError, match="Compromised CAPPO API credential detected"):
        Settings(
            _env_file=None,
            environment="production",
            api_keys="key1,cappo_internal_exec_key_veklom_2026,key3",
        )


def test_compromised_key_rejected_in_development(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")

    # Development must also fail closed: configuration never silently
    # manufactures a replacement credential.
    with pytest.raises(ValidationError, match="Compromised CAPPO API credential detected"):
        Settings(
            _env_file=None,
            environment="development",
            api_keys="key1,cappo_internal_exec_key_veklom_2026,key3",
        )
