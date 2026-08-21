"""Regression coverage for the known-compromised CAPPO API key guard."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cappo_backend.config import Settings


COMPROMISED = "cappo_internal_exec_key_veklom_2026"


def test_exact_compromised_api_key_is_rejected_in_development() -> None:
    with pytest.raises(ValidationError, match="Compromised CAPPO API credential detected"):
        Settings(environment="development", api_keys=COMPROMISED)


def test_exact_compromised_api_key_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="Compromised CAPPO API credential detected"):
        Settings(environment="production", api_keys=COMPROMISED)


def test_compromised_api_key_inside_list_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Compromised CAPPO API credential detected"):
        Settings(api_keys=f"safe-key,{COMPROMISED},other-safe-key")


def test_safe_api_key_list_is_preserved() -> None:
    settings = Settings(api_keys="safe-key,other-safe-key")
    assert settings.api_key_set == frozenset({"safe-key", "other-safe-key"})


def test_distinct_key_containing_compromised_text_is_preserved() -> None:
    distinct = f"safe-{COMPROMISED}-suffix"
    settings = Settings(api_keys=f"safe-key,{distinct}")
    assert settings.api_key_set == frozenset({"safe-key", distinct})
