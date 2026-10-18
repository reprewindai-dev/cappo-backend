"""Vault credential encryption module using AES-256-GCM (AEAD).

Associated data binds the credential to the specific workspace, provider, profile,
and key version, preventing ciphertext substitution or token-forwarding attacks.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DecryptionError(ValueError):
    """Raised when AEAD decryption fails due to corrupted data, wrong key, or associated data mismatch."""
    pass

def encrypt_secret(master_key: str, plaintext: str, associated_data: str) -> str:
    """Encrypt a secret using AES-GCM with associated data.

    Returns the formatted string 'nonce_b64:ciphertext_b64'.
    """
    if not master_key or not master_key.strip():
        raise ValueError("Master key must not be empty")
    
    # Deriving 256-bit key from master key
    key = hashlib.sha256(master_key.encode("utf-8")).digest()
    aesgcm = AESGCM(key)
    
    # 12-byte standard nonce for GCM
    nonce = os.urandom(12)
    
    ciphertext = aesgcm.encrypt(
        nonce,
        plaintext.encode("utf-8"),
        associated_data.encode("utf-8")
    )
    
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    ciphertext_b64 = base64.b64encode(ciphertext).decode("ascii")
    return f"{nonce_b64}:{ciphertext_b64}"

def decrypt_secret(master_key: str, encrypted_secret: str, associated_data: str) -> str:
    """Decrypt a secret using AES-GCM with associated data.

    Raises DecryptionError if authentication tag verification fails.
    """
    if not master_key or not master_key.strip():
        raise ValueError("Master key must not be empty")
    if not encrypted_secret or ":" not in encrypted_secret:
        raise DecryptionError("Invalid encrypted secret format")

    try:
        key = hashlib.sha256(master_key.encode("utf-8")).digest()
        aesgcm = AESGCM(key)
        
        parts = encrypted_secret.split(":", 1)
        nonce = base64.b64decode(parts[0])
        ciphertext = base64.b64decode(parts[1])
        
        decrypted = aesgcm.decrypt(
            nonce,
            ciphertext,
            associated_data.encode("utf-8")
        )
        return decrypted.decode("utf-8")
    except Exception as exc:
        raise DecryptionError(
            "Credential decryption failed. AEAD tag mismatch, wrong key, or associated data mismatch."
        ) from exc
