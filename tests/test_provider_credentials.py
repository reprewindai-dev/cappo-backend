"""Operator registration of workspace model providers (services/provider_credentials.py)."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.models.tenant_provider_credential import TenantProviderCredential
from cappo_backend.security.ssrf import SSRFValidationError
from cappo_backend.services.provider_credentials import register_provider_credential
from cappo_backend.services.providers import build_executor

SETTINGS = Settings(
    environment="test",
    executor_mode="live",
    vault_master_key="test-vault-master-key-0123456789abcdef",
)


def _provider(executor, name):
    return next(p for p in executor._providers if p.name == name)


def test_registered_ollama_becomes_a_workspace_provider(db: Session):
    registered = register_provider_credential(
        db, SETTINGS, workspace_id="w1", provider="Ollama", base_url="http://127.0.0.1:11434"
    )

    assert registered.provider == "ollama"
    assert registered.has_secret is False
    executor = build_executor(SETTINGS, db=db, workspace_id="w1")
    assert [p.name for p in executor._providers] == ["ollama"]
    # Another workspace does not see it.
    other = build_executor(SETTINGS, db=db, workspace_id="w2")
    assert "ollama" not in [p.name for p in other._providers]


def test_api_key_is_sealed_and_rotation_bumps_the_key_version(db: Session):
    register_provider_credential(
        db, SETTINGS, workspace_id="w1", provider="openai",
        base_url="https://1.1.1.1/v1", api_key="sk-first",
    )
    rotated = register_provider_credential(
        db, SETTINGS, workspace_id="w1", provider="openai",
        base_url="https://1.1.1.1/v1", api_key="sk-second",
    )

    assert rotated.key_version == 2
    row = db.query(TenantProviderCredential).one()
    assert "sk-second" not in row.encrypted_secret
    executor = build_executor(SETTINGS, db=db, workspace_id="w1")
    api_key = _provider(executor, "openai").executor._api_key
    assert api_key() == "sk-second"


def test_ollama_requires_a_private_endpoint(db: Session):
    with pytest.raises(SSRFValidationError):
        register_provider_credential(
            db, SETTINGS, workspace_id="w1", provider="ollama", base_url="http://1.1.1.1:11434"
        )
    with pytest.raises(ValueError):
        register_provider_credential(db, SETTINGS, workspace_id="w1", provider="ollama")
    assert db.query(TenantProviderCredential).count() == 0


def test_external_provider_must_be_public_https(db: Session):
    with pytest.raises(SSRFValidationError):
        register_provider_credential(
            db, SETTINGS, workspace_id="w1", provider="openai", base_url="http://1.1.1.1/v1"
        )
    with pytest.raises(SSRFValidationError):
        register_provider_credential(
            db, SETTINGS, workspace_id="w1", provider="openai", base_url="https://127.0.0.1/v1"
        )
