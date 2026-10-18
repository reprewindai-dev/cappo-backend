"""Adversarial tests for P0-2 (JIT Credential Broker) and P0-3 (Provider Authority & Failure Taxonomy)."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.models.tenant_provider_credential import TenantProviderCredential
from cappo_backend.security.vault import encrypt_secret
from cappo_backend.services.providers import (
    OpenAICompatExecutor,
    build_executor,
)

_MASTER_KEY = "test-master-key-of-at-least-32-chars-long"
_WORKSPACE = "ws-test-a"

@pytest.fixture
def jit_settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        ei_signing_key="test-signing-key-of-at-least-48-hex-characters-long",
        environment="test",
        auth_enabled=True,
        api_keys="test-api-key",
        executor_mode="openai",
        vault_master_key=_MASTER_KEY,
        allow_legacy_global_provider_config=False,
    )

def test_wrong_workspace_associated_data_fails_decryption(db: Session, jit_settings: Settings) -> None:
    # 1. Create credential encrypted for ws-test-a
    secret_key = "sk-super-secret"
    associated_data = f"{_WORKSPACE}:openai:default:1"
    encrypted = encrypt_secret(_MASTER_KEY, secret_key, associated_data)
    
    cred = TenantProviderCredential(
        id="cred-1",
        workspace_id=_WORKSPACE,
        provider="openai",
        credential_profile="default",
        encrypted_secret=encrypted,
        key_version=1,
    )
    db.add(cred)
    db.commit()
    
    # 2. Try to build executor for ws-test-b using ws-test-a's credential
    # But wait, query filter is on workspace_id, so let's simulate by modifying the workspace_id of query or calling decrypt directly
    from cappo_backend.security.vault import DecryptionError, decrypt_secret
    
    wrong_ad = "ws-test-b:openai:default:1"
    with pytest.raises(DecryptionError):
        decrypt_secret(_MASTER_KEY, encrypted, wrong_ad)

def test_credential_not_decrypted_during_construction(db: Session, jit_settings: Settings) -> None:
    # Build a credential record in the DB
    secret_key = "sk-super-secret"
    associated_data = f"{_WORKSPACE}:openai:default:1"
    encrypted = encrypt_secret(_MASTER_KEY, secret_key, associated_data)
    
    cred = TenantProviderCredential(
        id="cred-1",
        workspace_id=_WORKSPACE,
        provider="openai",
        credential_profile="default",
        encrypted_secret=encrypted,
        key_version=1,
    )
    db.add(cred)
    db.commit()
    
    # Construct executor
    executor = build_executor(jit_settings, db=db, workspace_id=_WORKSPACE)
    
    # Assert that no decryption has occurred yet (the executor stores a JIT resolver callable, not the plaintext)
    provider = executor._providers[0]
    assert provider.name == "openai"
    assert isinstance(provider.executor, OpenAICompatExecutor)
    assert callable(provider.executor._api_key)
    
    # Decryption should only occur on calling the resolver
    resolved_key = provider.executor._api_key()
    assert resolved_key == secret_key

def test_credential_rotation_key_version_mismatch(db: Session, jit_settings: Settings) -> None:
    # Encrypt secret with key_version 1
    secret_key = "sk-super-secret"
    associated_data = f"{_WORKSPACE}:openai:default:1"
    encrypted = encrypt_secret(_MASTER_KEY, secret_key, associated_data)
    
    # Store in DB with key_version 2 (simulating mismatched key_version or corrupted record)
    cred = TenantProviderCredential(
        id="cred-1",
        workspace_id=_WORKSPACE,
        provider="openai",
        credential_profile="default",
        encrypted_secret=encrypted,
        key_version=2,
    )
    db.add(cred)
    db.commit()
    
    executor = build_executor(jit_settings, db=db, workspace_id=_WORKSPACE)
    provider = executor._providers[0]
    
    from cappo_backend.security.vault import DecryptionError
    with pytest.raises(DecryptionError):
        provider.executor._api_key()

def test_aead_secret_never_logged_on_error(db: Session, jit_settings: Settings, caplog) -> None:
    # Encrypt secret
    secret_key = "sk-super-secret"
    associated_data = f"{_WORKSPACE}:openai:default:1"
    encrypted = encrypt_secret(_MASTER_KEY, secret_key, associated_data)
    
    cred = TenantProviderCredential(
        id="cred-1",
        workspace_id=_WORKSPACE,
        provider="openai",
        credential_profile="default",
        encrypted_secret=encrypted,
        key_version=1,
        base_url="https://api.openai.com/v1"
    )
    db.add(cred)
    db.commit()
    
    executor = build_executor(jit_settings, db=db, workspace_id=_WORKSPACE)
    
    request = {
        "prompt": "hello",
        "authority_envelope": {
            "execution_id": "exec-1",
            "allowed_provider_set": ["openai"],
        }
    }
    
    with caplog.at_level(logging.WARNING):
        try:
            executor.execute(request)
        except Exception:
            pass
            
        # Verify that the plaintext secret_key is NOT present anywhere in the logs
        log_text = caplog.text
        assert secret_key not in log_text
        assert encrypted not in log_text

def test_three_state_authority_logic_allowed_providers_none(db: Session, jit_settings: Settings) -> None:
    executor = build_executor(jit_settings, db=db, workspace_id=_WORKSPACE)
    
    # Missing allowed_provider_set in envelope -> None
    request = {
        "prompt": "hello",
        "authority_envelope": {
            "execution_id": "exec-1",
            # allowed_provider_set is missing
        }
    }
    
    from cappo_backend.services.executor import AuthorityContextMissingError
    with pytest.raises(AuthorityContextMissingError) as exc_info:
        executor.execute(request)
    assert exc_info.value.error_code == "AUTHORITY_CONTEXT_MISSING"

def test_three_state_authority_logic_allowed_providers_empty(db: Session, jit_settings: Settings) -> None:
    executor = build_executor(jit_settings, db=db, workspace_id=_WORKSPACE)
    
    # Empty allowed_provider_set -> set()
    request = {
        "prompt": "hello",
        "authority_envelope": {
            "execution_id": "exec-1",
            "allowed_provider_set": [],
        }
    }
    
    from cappo_backend.services.executor import ProviderNotAuthorizedError
    with pytest.raises(ProviderNotAuthorizedError) as exc_info:
        executor.execute(request)
    assert exc_info.value.error_code == "PROVIDER_NOT_AUTHORIZED"

def test_authorized_provider_not_configured(db: Session, jit_settings: Settings) -> None:
    # 1. Configured providers has openai only
    cred = TenantProviderCredential(
        id="cred-1",
        workspace_id=_WORKSPACE,
        provider="openai",
        credential_profile="default",
        encrypted_secret=encrypt_secret(_MASTER_KEY, "sk-test", f"{_WORKSPACE}:openai:default:1"),
        key_version=1,
    )
    db.add(cred)
    db.commit()
    
    executor = build_executor(jit_settings, db=db, workspace_id=_WORKSPACE)
    
    # 2. Request authorizes groq only
    request = {
        "prompt": "hello",
        "authority_envelope": {
            "execution_id": "exec-1",
            "allowed_provider_set": ["groq"],
        }
    }
    
    from cappo_backend.services.executor import AuthorizedProviderNotConfiguredError
    with pytest.raises(AuthorizedProviderNotConfiguredError) as exc_info:
        executor.execute(request)
    assert exc_info.value.error_code == "AUTHORIZED_PROVIDER_NOT_CONFIGURED"
