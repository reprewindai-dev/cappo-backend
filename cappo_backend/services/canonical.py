"""Canonical serialization, hashing, and signing helpers.

Lineage seed: the old backend's ``_sha256_json()`` provenance helper and the
``AIAuditLog`` HMAC-SHA256 ``log_hash`` pattern. CAPPO promotes these to a shared,
deterministic primitive used by PGL certificates, the ledger, and
ExecutionIdentityV1.

Determinism rule: the same logical payload must always produce the same hash and
signature. We therefore serialize with sorted keys, no insignificant whitespace,
and ``ensure_ascii=False`` so equivalent unicode is stable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)


def canonical_json(payload: Any) -> str:
    """Serialize ``payload`` to a canonical, deterministic JSON string."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def sha256_json(payload: Any) -> str:
    """Return the hex SHA-256 of the canonical JSON of ``payload``."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def get_ed25519_private_key(seed_str: str) -> ed25519.Ed25519PrivateKey:
    """Derive a deterministic 32-byte Ed25519 private key from any string seed."""
    seed = hashlib.sha256(seed_str.encode("utf-8")).digest()
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed)


def sign_payload_ed25519(payload: Any, private_key_or_seed: Any) -> str:
    """Sign payload using Ed25519 and return Base64url-encoded signature."""
    if isinstance(private_key_or_seed, str):
        private_key = get_ed25519_private_key(private_key_or_seed)
    else:
        private_key = private_key_or_seed

    serialized = canonical_json(payload).encode("utf-8")
    sig_bytes = private_key.sign(serialized)
    return base64.urlsafe_b64encode(sig_bytes).decode("utf-8").rstrip("=")


def verify_signature_ed25519(payload: Any, signature: str, public_key_or_seed: Any) -> bool:
    """Verify Base64url Ed25519 signature."""
    is_ed25519_ok = False
    try:
        if isinstance(public_key_or_seed, str):
            priv_key = get_ed25519_private_key(public_key_or_seed)
            public_key = priv_key.public_key()
        elif isinstance(public_key_or_seed, bytes):
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_or_seed)
        else:
            public_key = public_key_or_seed

        serialized = canonical_json(payload).encode("utf-8")
        # Add padding back to base64url if needed
        rem = len(signature) % 4
        signature_padded = signature + "=" * (4 - rem) if rem > 0 else signature
        sig_bytes = base64.urlsafe_b64decode(signature_padded.encode("utf-8"))
        public_key.verify(sig_bytes, serialized)
        is_ed25519_ok = True
    except Exception as e:
        logger.debug("Ed25519 signature verification failed", exc_info=True)

    if is_ed25519_ok:
        return True

    # Fallback to HMAC verification if key is string
    if isinstance(public_key_or_seed, str):
        try:
            if verify_signature_hmac(payload, signature, public_key_or_seed):
                return True
        except Exception as e:
            logger.debug("Fallback signature verification failed", exc_info=True)
        try:
            if verify_signature(payload, signature, public_key_or_seed):
                return True
        except Exception as e:
            logger.debug("Fallback signature verification failed", exc_info=True)

    return False


def sign_payload_hmac(payload: Any, hmac_key: str) -> str:
    """Return a Base64url-encoded HMAC-SHA256 signature over the canonical JSON of ``payload``."""
    serialized = canonical_json(payload).encode("utf-8")
    sig_bytes = hmac.new(
        hmac_key.encode("utf-8"),
        serialized,
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(sig_bytes).decode("utf-8").rstrip("=")


def verify_signature_hmac(payload: Any, signature: str, hmac_key: str) -> bool:
    """Verify Base64url-encoded HMAC signature."""
    expected = sign_payload_hmac(payload, hmac_key)
    return hmac.compare_digest(expected, signature)


def sign_payload(payload: Any, signing_key: str) -> str:
    """Legacy helper: Return a hex HMAC-SHA256 signature over canonical JSON."""
    return hmac.new(
        signing_key.encode("utf-8"),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(payload: Any, signature: str, signing_key: str) -> bool:
    """Legacy helper: Constant-time verification of legacy signatures."""
    expected = sign_payload(payload, signing_key)
    if hmac.compare_digest(expected, signature):
        return True
    try:
        if verify_signature_hmac(payload, signature, signing_key):
            return True
    except Exception as e:
        logger.debug("Legacy signature verification failed", exc_info=True)
    return False

