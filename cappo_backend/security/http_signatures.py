"""Strict RFC 9421 verification for CAPPO requests and provider responses."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, datetime
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key


class SignatureVerificationError(RuntimeError):
    """The signed HTTP message failed the configured integrity profile."""


def verify_rfc9421_response(
    *,
    status_code: int,
    headers: Mapping[str, str],
    body: bytes,
    public_key_hex: str,
    max_age_seconds: int = 5,
) -> None:
    """Verify the Veklom provider-failover response profile."""
    if status_code != 503:
        raise SignatureVerificationError("only HTTP 503 may be a failover signal")
    _verify_message(
        headers=headers,
        body=body,
        public_key_hex=public_key_hex,
        required_components={"@status", "content-digest"},
        derived={"@status": str(status_code)},
        max_age_seconds=max_age_seconds,
    )


def verify_rfc9421_request(
    *,
    method: str,
    target_uri: str,
    headers: Mapping[str, str],
    body: bytes,
    public_key_hex: str,
    max_age_seconds: int = 5,
    required_header_components: set[str] | None = None,
) -> None:
    """Verify the CAPPO consequence-bearing request signature profile.

    The mandatory derived components bind method, exact target and body digest.
    Consequence callers can additionally require trust-bearing headers to be
    signature-covered so identity/authority metadata cannot be swapped after
    the trusted intermediary signs the request.
    """
    if method.upper() != "POST":
        raise SignatureVerificationError("only POST may use the CAPPO execution request profile")
    required = {"@method", "@target-uri", "content-digest"}
    required.update(component.lower() for component in (required_header_components or set()))
    _verify_message(
        headers=headers,
        body=body,
        public_key_hex=public_key_hex,
        required_components=required,
        derived={"@method": method.upper(), "@target-uri": target_uri},
        max_age_seconds=max_age_seconds,
    )


def _verify_message(
    *,
    headers: Mapping[str, str],
    body: bytes,
    public_key_hex: str,
    required_components: set[str],
    derived: Mapping[str, str],
    max_age_seconds: int,
) -> None:
    if not public_key_hex:
        raise SignatureVerificationError("no federation public key configured")
    public_key = _load_ed25519_public_key(public_key_hex)

    normalized = {key.lower(): value for key, value in headers.items()}
    content_digest = normalized.get("content-digest")
    expected_digest = f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode('ascii')}:"
    if content_digest != expected_digest:
        raise SignatureVerificationError("content-digest mismatch")

    signature_input = normalized.get("signature-input")
    signature = normalized.get("signature")
    if not signature_input or not signature:
        raise SignatureVerificationError("missing Signature or Signature-Input")

    input_match = re.fullmatch(r"sig1=\((?P<fields>[^)]*)\)(?P<params>(?:;[^;=]+(?:=[^;]+)?)+)", signature_input)
    if not input_match:
        raise SignatureVerificationError("invalid Signature-Input format")
    fields = tuple(field.lower() for field in re.findall(r'"([^"]+)"', input_match.group("fields")))
    if not required_components.issubset(fields):
        required = ", ".join(sorted(required_components))
        raise SignatureVerificationError(f"signature must cover {required}")

    created_match = re.search(r"(?:^|;)created=(\d+)(?:;|$)", input_match.group("params"))
    if not created_match:
        raise SignatureVerificationError("Signature-Input missing created timestamp")
    created = int(created_match.group(1))
    if abs(int(datetime.now(UTC).timestamp()) - created) > max_age_seconds:
        raise SignatureVerificationError("signature expired")

    base_lines: list[str] = []
    for field in fields:
        if field in derived:
            base_lines.append(f'"{field}": {derived[field]}')
            continue
        value = normalized.get(field)
        if value is None:
            raise SignatureVerificationError(f"signed field {field} missing from headers")
        base_lines.append(f'"{field}": {value}')
    base_lines.append(f'"@signature-params": ({input_match.group("fields")}){input_match.group("params")}')

    signature_match = re.fullmatch(r"sig1=:([^:]+):", signature)
    if not signature_match:
        raise SignatureVerificationError("invalid Signature format")
    try:
        encoded = signature_match.group(1)
        signature_bytes = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
    except Exception as exc:
        raise SignatureVerificationError("invalid signature encoding") from exc
    try:
        public_key.verify(signature_bytes, "\n".join(base_lines).encode())
    except InvalidSignature as exc:
        raise SignatureVerificationError("invalid signature") from exc


def _load_ed25519_public_key(encoded_key: str) -> Ed25519PublicKey:
    """Load raw hexadecimal or Base64 SPKI Ed25519 public keys."""
    try:
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(encoded_key))
    except ValueError:
        pass

    try:
        decoded = base64.b64decode(encoded_key, validate=True)
        public_key = load_der_public_key(decoded)
    except (TypeError, ValueError) as exc:
        raise SignatureVerificationError("invalid federation public key format") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise SignatureVerificationError("federation public key must be Ed25519")
    return public_key
