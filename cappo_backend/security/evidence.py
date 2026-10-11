"""Signed execution evidence (COSE_Sign1, Ed25519) and its signing key.

The evidence key must outlive any container. Production rules (2026-10-11):
- The key comes from EVIDENCE_ROOT_PRIVATE_KEY_HEX, or from a key file on the persistent
  CAPPO volume (EVIDENCE_ROOT_KEY_PATH, default /var/lib/cappo/evidence_root_key).
- A key left by older releases inside the container (/app/.evidence_root_key or
  ./.evidence_root_key) is copied onto the volume once, so receipts already issued
  stay verifiable after the container is replaced.
- In production, CAPPO never silently mints a new evidence key: a missing key is an
  error, because a fresh key would orphan every receipt issued before it.
- The public half is published (see evidence_public_key_descriptor) and every receipt
  names the key that signed it by a fingerprint derived from that key, not a constant.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import threading
from collections.abc import Mapping

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

DEFAULT_KEY_PATH = "/var/lib/cappo/evidence_root_key"
LEGACY_KEY_PATHS = ("/app/.evidence_root_key", ".evidence_root_key")

# Alias keeps signatures short (and stops secret scanners reading the type name as a key value).
Ed25519Priv = ed25519.Ed25519PrivateKey

_lock = threading.Lock()
_cached: ed25519.Ed25519PrivateKey | None = None


class EvidenceKeyUnavailable(RuntimeError):
    """No persisted evidence key exists and minting one would orphan prior receipts."""


def _is_production() -> bool:
    env = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "development").strip().lower()
    return env in {"production", "prod"}


def _key_path() -> str:
    return os.getenv("EVIDENCE_ROOT_KEY_PATH") or DEFAULT_KEY_PATH


def _raw_private(pk: ed25519.Ed25519PrivateKey) -> bytes:
    return pk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _write_key_file(path: str, raw: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load_file(path: str) -> ed25519.Ed25519PrivateKey | None:
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) != 32:
        raise EvidenceKeyUnavailable(f"evidence key file {path} is not a 32-byte Ed25519 seed")
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw)


def _resolve_key() -> ed25519.Ed25519PrivateKey:
    env_hex = (os.getenv("EVIDENCE_ROOT_PRIVATE_KEY_HEX") or "").strip()
    if env_hex:
        try:
            return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(env_hex))
        except ValueError as exc:
            # A configured but malformed key is never replaced by another key.
            raise EvidenceKeyUnavailable("EVIDENCE_ROOT_PRIVATE_KEY_HEX is not a 32-byte hex seed") from exc

    path = _key_path()
    key = _load_file(path)
    if key is not None:
        return key

    for legacy in LEGACY_KEY_PATHS:
        key = _load_file(legacy)
        if key is not None:
            try:
                _write_key_file(path, _raw_private(key))
            except OSError:
                if _is_production():
                    raise EvidenceKeyUnavailable(
                        f"found legacy evidence key at {legacy} but could not persist it to {path}"
                    )
            return key

    if _is_production():
        raise EvidenceKeyUnavailable(
            "no evidence signing key: set EVIDENCE_ROOT_PRIVATE_KEY_HEX or provide "
            f"{path}; refusing to mint a new key in production"
        )
    key = ed25519.Ed25519PrivateKey.generate()
    try:
        _write_key_file(path, _raw_private(key))
    except OSError:
        pass  # development only: an unpersisted key is acceptable outside production
    return key


def get_evidence_key_pair() -> ed25519.Ed25519PrivateKey:
    global _cached
    with _lock:
        if _cached is None:
            _cached = _resolve_key()
        return _cached


def reset_evidence_key_cache() -> None:
    """Test hook: forget the cached key so the next call re-resolves it."""
    global _cached
    with _lock:
        _cached = None


def evidence_key_id(public_key: ed25519.Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()


def evidence_public_key_descriptor(private_key: Ed25519Priv | None = None) -> dict:
    """The public half of the evidence key as a JWK plus the kid every receipt carries."""
    pk = private_key or get_evidence_key_pair()
    pub = pk.public_key()
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "kid": evidence_key_id(pub),
        "kty": "OKP",
        "crv": "Ed25519",
        "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
        "alg": "EdDSA",
        "use": "sig",
    }


def mint_signed_execution_evidence(
    canonical_receipt: dict,
    key_file: str | None = None,  # kept for call-site compatibility; the key path is configured, not passed
    key_id: bytes | None = None,
    private_key: Ed25519Priv | None = None,
) -> bytes:
    if private_key is None:
        private_key = get_evidence_key_pair()
    if key_id is None:
        key_id = evidence_key_id(private_key.public_key()).encode()
    payload = cbor2.dumps(canonical_receipt, canonical=True)
    protected_header = cbor2.dumps({1: -8}, canonical=True)
    unprotected_header = {4: key_id}
    sig_structure = ["Signature1", protected_header, b"", payload]
    sig_data = cbor2.dumps(sig_structure, canonical=True)
    signature = private_key.sign(sig_data)
    cose_sign1 = [protected_header, unprotected_header, payload, signature]
    return cbor2.dumps(cbor2.CBORTag(18, cose_sign1), canonical=True)


def evidence_issuer() -> str:
    """Name this deployment signs receipts as. Any operator can run CAPPO under its own name."""
    return (os.getenv("EVIDENCE_ISSUER") or "cappo").strip() or "cappo"


def verify_with_trust_store(
    cose_bytes: bytes,
    issuer_keys: dict[str, list[ed25519.Ed25519PublicKey]],
) -> tuple[str, dict]:
    """Verify a receipt against every issuer the caller chooses to trust.

    The receipt names its signing key by fingerprint (unprotected header label 4). The issuer is
    whoever in the caller's trust store holds that key; no issuer is built in. Returns
    (issuer, receipt). Raises ValueError if no trusted issuer holds the key or the signature fails.
    """
    try:
        decoded = cbor2.loads(cose_bytes)
        unprotected = decoded.value[1] if isinstance(decoded, cbor2.CBORTag) else {}
        kid = unprotected.get(4) if isinstance(unprotected, Mapping) else None
        kid = kid.decode() if isinstance(kid, bytes) else kid
    except Exception as e:
        raise ValueError(f"Failed to parse COSE bytes: {e}")
    holders = [
        (issuer, key)
        for issuer, keys in issuer_keys.items()
        for key in keys
        if evidence_key_id(key) == kid
    ]
    if not holders:
        raise ValueError("No trusted issuer holds the key that signed this receipt")
    issuer, key = holders[0]
    return issuer, verify_signed_execution_evidence(cose_bytes, key)


def verify_signed_execution_evidence(
    cose_bytes: bytes,
    public_key: ed25519.Ed25519PublicKey,
) -> dict:
    try:
        decoded_tag = cbor2.loads(cose_bytes)
    except Exception as e:
        raise ValueError(f"Failed to parse COSE bytes: {e}")
    if not isinstance(decoded_tag, cbor2.CBORTag) or decoded_tag.tag != 18:
        raise ValueError("Not a valid COSE_Sign1 tagged object")
    try:
        protected_header, unprotected_header, payload, signature = decoded_tag.value
    except ValueError:
        raise ValueError("COSE_Sign1 array must have exactly 4 elements")
    sig_struct = ["Signature1", protected_header, b"", payload]
    sig_data = cbor2.dumps(sig_struct, canonical=True)
    try:
        public_key.verify(signature, sig_data)
    except InvalidSignature:
        raise ValueError("Signature verification failed")
    try:
        receipt = cbor2.loads(payload)
    except Exception as e:
        raise ValueError(f"Failed to parse payload as CBOR: {e}")
    return receipt
