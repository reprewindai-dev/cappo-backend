"""Signed, expiring capability advertisements."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives import serialization

from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.config import Settings, get_settings
from cappo_backend.services.canonical import (
    get_ed25519_private_key,
    sha256_json,
    sign_payload_ed25519,
    verify_signature_ed25519,
)


def _public_key(seed: str) -> str:
    raw = (
        get_ed25519_private_key(seed)
        .public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _key_seeds(settings: Settings) -> dict[str, str]:
    if not settings.capability_beacon_keys_json:
        return {settings.capability_beacon_kid: settings.ei_signing_key}
    value = json.loads(settings.capability_beacon_keys_json)
    if not isinstance(value, dict) or not value:
        raise ValueError("CAPABILITY_BEACON_KEYS_JSON must be a non-empty object")
    return {str(k): str(v) for k, v in value.items()}


def active_signing_seed(settings: Settings) -> str:
    """Return the private seed matching the advertised active key id."""
    seeds = _key_seeds(settings)
    try:
        return seeds[settings.capability_beacon_kid]
    except KeyError as exc:
        raise ValueError("CAPABILITY_BEACON_KID is absent from CAPABILITY_BEACON_KEYS_JSON") from exc


def published_keys(settings: Settings | None = None) -> list[dict[str, str]]:
    settings = settings or get_settings()
    return [
        {
            "kid": kid,
            "kty": "OKP",
            "crv": "Ed25519",
            "algorithm": "EdDSA",
            "public_key": _public_key(seed),
        }
        for kid, seed in _key_seeds(settings).items()
    ]


def _parse_expiry(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_beacon(package: CapabilityPackage, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    seeds = _key_seeds(settings)
    kid = settings.capability_beacon_kid
    if kid not in seeds:
        raise ValueError("CAPABILITY_BEACON_KID is not present in CAPABILITY_BEACON_KEYS_JSON")
    signing_seed = seeds[kid]

    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at.timestamp() + max(1, settings.capability_beacon_ttl_seconds)
    issued = issued_at.isoformat()
    expires = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    body: dict[str, Any] = {
        "capability_id": package.family,
        "capability_version": package.id.rsplit("@", 1)[-1],
        "package_ref": package.id,
        "policy_hash": sha256_json(package.policy_defaults),
        "issued_at": issued,
        "expires_at": expires,
        "issuer": settings.capability_beacon_issuer,
        "kid": kid,
        "issuer_public_key": _public_key(signing_seed),
    }
    body["signature"] = sign_payload_ed25519(body, signing_seed)
    return body


def verify_beacon(
    beacon: dict[str, Any],
    settings: Settings | None = None,
) -> tuple[bool, str, str | None]:
    settings = settings or get_settings()
    signature = beacon.get("signature")
    if not isinstance(signature, str):
        return False, "signature_missing", None
    if beacon.get("issuer") != settings.capability_beacon_issuer:
        return False, "issuer_mismatch", None
    try:
        expires_at = _parse_expiry(str(beacon["expires_at"]))
    except (KeyError, TypeError, ValueError):
        return False, "expiry_invalid", None
    if expires_at <= datetime.now(timezone.utc):
        return False, "beacon_expired", None
    body = {key: value for key, value in beacon.items() if key != "signature"}
    kid = beacon.get("kid")
    seeds = _key_seeds(settings)
    if not isinstance(kid, str) or kid not in seeds:
        return False, "unknown_kid", None
    expected_public_key = _public_key(seeds[kid])
    if beacon.get("issuer_public_key") != expected_public_key:
        return False, "issuer_key_mismatch", None
    raw_key = base64.urlsafe_b64decode(expected_public_key + "===")
    if not verify_signature_ed25519(body, signature, raw_key):
        return False, "signature_invalid", None
    return True, "verified", kid
