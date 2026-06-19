from __future__ import annotations

from unittest.mock import patch

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient
from web3 import Web3

from cappo_backend.api.routers.x402_router import (
    MERCHANT_WALLET,
    _pending_tx_hashes,
    _verified_tx_hashes,
)


@pytest.fixture(autouse=True)
def clean_x402_state():
    """Clear memory registers before and after every test run."""
    _verified_tx_hashes.clear()
    _pending_tx_hashes.clear()
    yield
    _verified_tx_hashes.clear()
    _pending_tx_hashes.clear()


class TestX402Payments:
    def test_x402_config_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/v1/x402/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["x402Version"] == 1
        assert data["merchant_wallet"] == MERCHANT_WALLET

    def test_unauthorized_request_returns_402_challenge(self, client: TestClient) -> None:
        # 1. Missing wallet address
        resp = client.post("/api/v1/x402/discovery/unlock", json={"feature_id": "premium"})
        assert resp.status_code == 400
        assert "X-Wallet-Address" in resp.json()["detail"]["error"]

        # 2. Wallet present, payment missing -> returns 402 spec challenge
        wallet = "0x" + "1" * 40
        resp = client.post(
            "/api/v1/x402/discovery/unlock",
            json={"feature_id": "premium"},
            headers={"x-wallet-address": wallet}
        )
        assert resp.status_code == 402
        assert resp.json()["detail"]["x402Version"] == 1
        assert "accepts" in resp.json()["detail"]

    @patch("cappo_backend.api.routers.x402_router._verify_onchain")
    def test_valid_eip712_signature_and_tx_hash_unlocks(self, mock_verify, client: TestClient) -> None:
        mock_verify.return_value = (True, None, "123456")

        # Create wallet and signing account
        acct = Account.create()
        caller_wallet = acct.address

        tx_hash = "0x" + "b" * 64
        nonce_bytes = Web3.to_bytes(hexstr=tx_hash)
        amount_units = 10_000  # Unlock endpoint price is $0.01 (10,000 micro-USDC)
        body_data = {"feature_id": "premium-metrics"}
        body_bytes = b'{"feature_id":"premium-metrics"}'  # Exact raw representation without space for hashing consistency
        body_hash = Web3.keccak(body_bytes)

        # Build EIP-712 structured payload
        structured_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "PaymentRequirements": [
                    {"name": "recipient", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                    {"name": "method", "type": "string"},
                    {"name": "resource", "type": "string"},
                    {"name": "bodyHash", "type": "bytes32"},
                ],
            },
            "primaryType": "PaymentRequirements",
            "domain": {
                "name": "VNP x402 Billing Gateway",
                "version": "1",
                "chainId": 8453,
                "verifyingContract": MERCHANT_WALLET,
            },
            "message": {
                "recipient": MERCHANT_WALLET,
                "amount": amount_units,
                "nonce": nonce_bytes,
                "method": "POST",
                "resource": "/api/v1/x402/discovery/unlock",
                "bodyHash": body_hash,
            }
        }

        # Generate signature
        encoded_message = encode_typed_data(full_message=structured_data)
        signature = acct.sign_message(encoded_message).signature.hex()

        # Send request with headers
        headers = {
            "x-wallet-address": caller_wallet,
            "x-payment": tx_hash,
            "x-signature": signature
        }

        resp = client.post(
            "/api/v1/x402/discovery/unlock",
            json=body_data,
            headers=headers
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["wallet"] == caller_wallet.lower()

        # Try to replay same transaction -> Should fail with 400 already consumed
        resp_replay = client.post(
            "/api/v1/x402/discovery/unlock",
            json=body_data,
            headers=headers
        )
        assert resp_replay.status_code == 400
        assert "already used" in resp_replay.json()["detail"]["message"]

    @patch("cappo_backend.api.routers.x402_router._verify_onchain")
    def test_invalid_signature_context_rejected(self, mock_verify, client: TestClient) -> None:
        mock_verify.return_value = (True, None, "123456")

        acct = Account.create()
        caller_wallet = acct.address

        tx_hash = "0x" + "c" * 64
        nonce_bytes = Web3.to_bytes(hexstr=tx_hash)
        
        # Build signature targeting /api/v1/x402/discovery/unlock (cheap endpoint)
        structured_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "PaymentRequirements": [
                    {"name": "recipient", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                    {"name": "method", "type": "string"},
                    {"name": "resource", "type": "string"},
                    {"name": "bodyHash", "type": "bytes32"},
                ],
            },
            "primaryType": "PaymentRequirements",
            "domain": {
                "name": "VNP x402 Billing Gateway",
                "version": "1",
                "chainId": 8453,
                "verifyingContract": MERCHANT_WALLET,
            },
            "message": {
                "recipient": MERCHANT_WALLET,
                "amount": 10_000,
                "nonce": nonce_bytes,
                "method": "POST",
                "resource": "/api/v1/x402/discovery/unlock",  # TARGET PATH
                "bodyHash": Web3.keccak(b'{"feature_id":"premium-metrics"}'),
            }
        }
        encoded_message = encode_typed_data(full_message=structured_data)
        signature = acct.sign_message(encoded_message).signature.hex()

        # Send hijacked signature to /api/v1/x402/exec/run (premium endpoint)
        headers = {
            "x-wallet-address": caller_wallet,
            "x-payment": tx_hash,
            "x-signature": signature
        }

        resp = client.post(
            "/api/v1/x402/exec/run",
            json={"prompt": "launch", "agent_id": "test"},
            headers=headers
        )

        # Context-Binding mismatch yields HTTP 403 Forbidden!
        assert resp.status_code == 403
        assert "Context-Binding mismatch" in resp.json()["detail"]["message"]

    def test_pessimistic_nonce_concurrency_lock(self, client: TestClient) -> None:
        # Simulate an ongoing pending verification
        tx_hash = "0x" + "d" * 64
        _pending_tx_hashes.add(tx_hash)

        # Submit request with the pending tx hash
        resp = client.post(
            "/api/v1/x402/discovery/unlock",
            json={"feature_id": "premium"},
            headers={
                "x-wallet-address": "0x" + "2" * 40,
                "x-payment": tx_hash
            }
        )

        # Should yield HTTP 409 Conflict (nonce linearized)
        assert resp.status_code == 409
        assert "State lock collision" in resp.json()["detail"]["message"]
