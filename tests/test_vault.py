"""Tests for the AEAD vault encryption/decryption helper."""

from __future__ import annotations

import pytest
from cappo_backend.security.vault import encrypt_secret, decrypt_secret, DecryptionError

def test_vault_aead_encrypt_decrypt_success() -> None:
    master_key = "some-very-secret-vault-master-key"
    plaintext = "sk-proj-super-secret-api-key"
    associated_data = "ws-alpha:openai:default:1"
    
    encrypted = encrypt_secret(master_key, plaintext, associated_data)
    assert encrypted is not None
    assert ":" in encrypted
    
    decrypted = decrypt_secret(master_key, encrypted, associated_data)
    assert decrypted == plaintext

def test_vault_aead_tag_mismatch_wrong_associated_data() -> None:
    master_key = "some-very-secret-vault-master-key"
    plaintext = "sk-proj-super-secret-api-key"
    associated_data = "ws-alpha:openai:default:1"
    
    encrypted = encrypt_secret(master_key, plaintext, associated_data)
    
    # Try decrypting with modified associated data (e.g. wrong workspace)
    wrong_ad = "ws-beta:openai:default:1"
    with pytest.raises(DecryptionError, match="AEAD tag mismatch"):
        decrypt_secret(master_key, encrypted, wrong_ad)

def test_vault_aead_tag_mismatch_wrong_key() -> None:
    master_key = "some-very-secret-vault-master-key"
    plaintext = "sk-proj-super-secret-api-key"
    associated_data = "ws-alpha:openai:default:1"
    
    encrypted = encrypt_secret(master_key, plaintext, associated_data)
    
    wrong_key = "some-other-secret-vault-master-key"
    with pytest.raises(DecryptionError, match="AEAD tag mismatch"):
        decrypt_secret(wrong_key, encrypted, associated_data)

def test_vault_empty_key_rejected() -> None:
    with pytest.raises(ValueError, match="Master key must not be empty"):
        encrypt_secret("", "secret", "ad")
        
    with pytest.raises(ValueError, match="Master key must not be empty"):
        decrypt_secret("", "nonce:cipher", "ad")

def test_vault_invalid_format_rejected() -> None:
    with pytest.raises(DecryptionError, match="Invalid encrypted secret format"):
        decrypt_secret("key", "no-colon-format", "ad")
