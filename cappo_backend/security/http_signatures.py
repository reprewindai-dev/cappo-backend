"""Strict RFC 9421 response verification for provider failover signals."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, datetime
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class SignatureVerificationError(RuntimeError):
    """The provider response is not an authenticated failover signal."""


def verify_rfc9421_response(
    *,
    status_code: int,
    headers: Mapping[str, str],
    body: bytes,
    public_key_hex: str,
    max_age_seconds: int = 5,
) -> None:
    """Verify the Veklom provider-failover response profile.

    A usable 503 must bind its ``@status`` and ``content-digest`` with an
    Ed25519 RFC 9421 signature.  This establishes message integrity only; the
    executor separately enforces CAPPO's authorized-provider set.
    """
    if status_code != 503:
        raise SignatureVerificationError("only HTTP 503 may be a failover signal")
    if not public_key_hex:
        raise SignatureVerificationError("no federation public key configured")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    except ValueError as exc:
        raise SignatureVerificationError("invalid federation public key format") from exc

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
    fields = tuple(re.findall(r'"([^"]+)"', input_match.group("fields")))
    if "@status" not in fields or "content-digest" not in fields:
        raise SignatureVerificationError("signature must cover @status and content-digest")

    created_match = re.search(r"(?:^|;)created=(\d+)(?:;|$)", input_match.group("params"))
    if not created_match:
        raise SignatureVerificationError("Signature-Input missing created timestamp")
    created = int(created_match.group(1))
    if abs(int(datetime.now(UTC).timestamp()) - created) > max_age_seconds:
        raise SignatureVerificationError("signature expired")

    base_lines: list[str] = []
    for field in fields:
        if field == "@status":
            base_lines.append(f'"@status": {status_code}')
            continue
        value = normalized.get(field.lower())
        if value is None:
            raise SignatureVerificationError(f"signed field {field} missing from headers")
        base_lines.append(f'"{field.lower()}": {value}')
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
