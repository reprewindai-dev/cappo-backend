from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cappo_backend.security.http_signatures import (
    SignatureVerificationError,
    verify_rfc9421_response,
)


def _signed_503(private_key: Ed25519PrivateKey, body: bytes) -> dict[str, str]:
    created = int(datetime.now(UTC).timestamp())
    digest = f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode('ascii')}:"
    date = "Mon, 01 Jan 2026 12:00:00 GMT"
    signature_input = f'sig1=("@status" "content-digest" "date");created={created};keyid="provider-a"'
    base = "\n".join(
        [
            '"@status": 503',
            f'"content-digest": {digest}',
            f'"date": {date}',
            f'"@signature-params": ("@status" "content-digest" "date");created={created};keyid="provider-a"',
        ]
    ).encode()
    signature = base64.b64encode(private_key.sign(base)).decode()
    return {
        "content-digest": digest,
        "date": date,
        "signature-input": signature_input,
        "signature": f"sig1=:{signature}:",
    }


def test_valid_signed_503_is_accepted() -> None:
    private_key = Ed25519PrivateKey.generate()
    body = b'{"error":"unavailable"}'

    verify_rfc9421_response(
        status_code=503,
        headers=_signed_503(private_key, body),
        body=body,
        public_key_hex=private_key.public_key().public_bytes_raw().hex(),
    )


def test_changed_status_is_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    body = b'{"error":"unavailable"}'

    with pytest.raises(SignatureVerificationError, match="only HTTP 503"):
        verify_rfc9421_response(
            status_code=200,
            headers=_signed_503(private_key, body),
            body=body,
            public_key_hex=private_key.public_key().public_bytes_raw().hex(),
        )


def test_changed_body_is_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    body = b'{"error":"unavailable"}'

    with pytest.raises(SignatureVerificationError, match="content-digest mismatch"):
        verify_rfc9421_response(
            status_code=503,
            headers=_signed_503(private_key, body),
            body=b'{"error":"tampered"}',
            public_key_hex=private_key.public_key().public_bytes_raw().hex(),
        )
