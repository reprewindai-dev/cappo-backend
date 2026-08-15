"""Canonical serialization, hashing, and signing helpers.

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
from cryptography.hazmat.primitives.serialization import load_der_public_key

logger = logging.getLogger(__name__)


def canonical_json(payload: Any) -> str:
    """Serialize ``payload`` to a canonical, deterministic JSON string."""
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a dictionary")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def sha256_json(payload: Any) -> str:
    """Return the hex SHA-256 of the canonical JSON of ``payload``."""
    if not isinstance(payload, dict):
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
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
    """Strictly verify a Base64url Ed25519 signature.

    This function never falls back to HMAC or another signature scheme. Callers
    that require a legacy symmetric verifier must invoke that verifier explicitly;
    accepting another algorithm here would allow algorithm-confusion at an
    asymmetric trust boundary.
    """
    try:
        if not isinstance(signature, str) or not signature:
            return False
        if isinstance(public_key_or_seed, str):
            public_key = _public_key_from_config(public_key_or_seed)
        elif isinstance(public_key_or_seed, bytes):
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_or_seed)
        else:
            public_key = public_key_or_seed

        serialized = canonical_json(payload).encode("utf-8")
        rem = len(signature) % 4
        signature_padded = signature + "=" * (4 - rem) if rem > 0 else signature
        sig_bytes = base64.b64decode(
            signature_padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        if len(sig_bytes) != 64:
            return False
        public_key.verify(sig_bytes, serialized)
        return True
    except Exception:
        logger.debug("Ed25519 signature verification failed", exc_info=True)
        return False


def _public_key_from_config(value: str) -> ed25519.Ed25519PublicKey:
    """Resolve an explicit cAPI SPKI key before legacy seed compatibility.

    cAPI/Covenant publishes its Ed25519 public keys as Base64 SPKI DER.  Older
    CAPPO development tests use a seed string, so a value that is not a valid
    Ed25519 SPKI retains that explicitly limited compatibility behavior.
    """
    try:
        candidate = load_der_public_key(base64.b64decode(value, validate=True))
        if isinstance(candidate, ed25519.Ed25519PublicKey):
            return candidate
    except (ValueError, TypeError):
        pass
    return get_ed25519_private_key(value).public_key()


def sign_payload_hmac(payload: Any, hmac_key: str) -> str:
    """Return a Base64url-encoded HMAC-SHA256 signature over canonical JSON."""
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
    """Legacy helper: return a hex HMAC-SHA256 signature over canonical JSON."""
    return hmac.new(
        signing_key.encode("utf-8"),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(payload: Any, signature: str, signing_key: str) -> bool:
    """Legacy helper: constant-time verification of legacy signatures."""
    expected = sign_payload(payload, signing_key)
    if hmac.compare_digest(expected, signature):
        return True
    try:
        if verify_signature_hmac(payload, signature, signing_key):
            return True
    except Exception:
        logger.debug("Legacy signature verification failed", exc_info=True)
    return False
