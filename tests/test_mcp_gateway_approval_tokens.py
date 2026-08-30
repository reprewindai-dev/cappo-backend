from __future__ import annotations

from datetime import datetime, timedelta

from cappo_backend.config import Settings
from cappo_backend.security.mcp_gateway import MCPGateway
from cappo_backend.services.canonical import sign_payload_hmac


def _gateway(signing_key: str = "approval-token-test-key") -> MCPGateway:
    gateway = MCPGateway(
        settings=Settings(
            environment="test",
            approval_token_signing_key=signing_key,
        )
    )
    gateway.redis_client = None
    return gateway


def _token(gateway: MCPGateway, **overrides):
    payload = {
        "approver_id": "human-approval-1",
        "capability_id": "payments.release",
        "expires_at": (datetime.utcnow() + timedelta(minutes=5)).timestamp(),
        "nonce": "request-nonce-1",
        "policy_snapshot_id": "policy-snapshot-1",
        "request_hash": "request-hash-1",
    }
    payload.update(overrides)
    signature_body = gateway._approval_token_signature_payload(payload)
    payload["signature"] = sign_payload_hmac(
        signature_body,
        gateway.settings.approval_token_signing_key,
    )
    return payload


def test_bound_approval_token_accepts_hmac_signed_payload() -> None:
    gateway = _gateway()
    token = _token(gateway)

    is_valid, approver_id, error = gateway._validate_bound_approval_token(
        token,
        request_hash="request-hash-1",
        policy_snapshot_id="policy-snapshot-1",
        capability_id="payments.release",
        request_nonce="request-nonce-1",
    )

    assert is_valid is True
    assert approver_id == "human-approval-1"
    assert error == "Valid"


def test_bound_approval_token_rejects_placeholder_signature() -> None:
    gateway = _gateway()
    token = _token(gateway)
    token["signature"] = "valid_signature"

    is_valid, approver_id, error = gateway._validate_bound_approval_token(
        token,
        request_hash="request-hash-1",
        policy_snapshot_id="policy-snapshot-1",
        capability_id="payments.release",
        request_nonce="request-nonce-1",
    )

    assert is_valid is False
    assert approver_id is None
    assert "Invalid cryptographic signature" in error


def test_bound_approval_token_rejects_missing_verifier_key() -> None:
    gateway = _gateway(signing_key="")
    token = _token(_gateway())

    is_valid, approver_id, error = gateway._validate_bound_approval_token(
        token,
        request_hash="request-hash-1",
        policy_snapshot_id="policy-snapshot-1",
        capability_id="payments.release",
        request_nonce="request-nonce-1",
    )

    assert is_valid is False
    assert approver_id is None
    assert "not configured" in error


def test_bound_approval_token_rejects_payload_tampering_after_signature() -> None:
    gateway = _gateway()
    token = _token(gateway)
    token["approver_id"] = "different-human"

    is_valid, approver_id, error = gateway._validate_bound_approval_token(
        token,
        request_hash="request-hash-1",
        policy_snapshot_id="policy-snapshot-1",
        capability_id="payments.release",
        request_nonce="request-nonce-1",
    )

    assert is_valid is False
    assert approver_id is None
    assert "Invalid cryptographic signature" in error
