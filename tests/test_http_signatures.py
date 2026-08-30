from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from cappo_backend.security.http_signatures import (
    SignatureVerificationError,
    verify_rfc9421_request,
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


def test_valid_signed_exec_request_is_accepted() -> None:
    private_key = Ed25519PrivateKey.generate()
    body = b'{"prompt":"governed"}'
    headers = _signed_request(private_key, body)

    verify_rfc9421_request(
        method="POST",
        target_uri="https://cappo.veklom.com/v1/exec",
        headers=headers,
        body=body,
        public_key_hex=private_key.public_key().public_bytes_raw().hex(),
    )


def test_spki_base64_gatekeeper_key_is_accepted() -> None:
    """cAPI publishes Ed25519 public keys as SPKI DER Base64."""
    private_key = Ed25519PrivateKey.generate()
    body = b'{"prompt":"governed"}'
    spki_b64 = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")

    verify_rfc9421_request(
        method="POST",
        target_uri="https://cappo.veklom.com/v1/exec",
        headers=_signed_request(private_key, body),
        body=body,
        public_key_hex=spki_b64,
    )


def test_tampered_request_body_is_rejected_before_authority() -> None:
    private_key = Ed25519PrivateKey.generate()
    body = b'{"prompt":"governed"}'

    with pytest.raises(SignatureVerificationError, match="content-digest mismatch"):
        verify_rfc9421_request(
            method="POST",
            target_uri="https://cappo.veklom.com/v1/exec",
            headers=_signed_request(private_key, body),
            body=b'{"prompt":"altered"}',
            public_key_hex=private_key.public_key().public_bytes_raw().hex(),
        )


def _signed_request(private_key: Ed25519PrivateKey, body: bytes) -> dict[str, str]:
    created = int(datetime.now(UTC).timestamp())
    digest = f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode('ascii')}:"
    params = f';created={created};keyid="requester-1"'
    base = "\n".join(
        [
            '"@method": POST',
            '"@target-uri": https://cappo.veklom.com/v1/exec',
            f'"content-digest": {digest}',
            f'"@signature-params": ("@method" "@target-uri" "content-digest"){params}',
        ]
    ).encode()
    signature = base64.b64encode(private_key.sign(base)).decode()
    return {
        "content-digest": digest,
        "signature-input": f'sig1=("@method" "@target-uri" "content-digest"){params}',
        "signature": f"sig1=:{signature}:",
    }
