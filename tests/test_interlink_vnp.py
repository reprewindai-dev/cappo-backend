import pytest
import hmac
import hashlib
from httpx import AsyncClient
from datetime import datetime, timezone
import uuid
import json

from cappo_backend.config import get_settings

def test_authorize_slash_missing_signature(client):
    response = client.post(
        "/api/internal/interlink/vnp/authorize-slash",
        headers={
            "x-agent-id": "test-agent",
            "x-request-id": "test-req",
            "x-execution-identity": '{"principal": "test-principal"}',
            "x-capability-id": "test-cap",
            "x-target-url": "test-url",
            "x-payment": "BYPASS",
            "x-pgl-pre-cert": "test-cert"
        },
        json={
            "bond_id": str(uuid.uuid4()),
            "challenge_id": str(uuid.uuid4()),
            "pgl_evidence_id": "pgl_123"
        }
    )
    assert response.status_code == 401

def test_authorize_slash_invalid_signature(client):
    response = client.post(
        "/api/internal/interlink/vnp/authorize-slash",
        headers={
            "x-vnp-signature": "bad_signature",
            "x-vnp-timestamp": datetime.now(timezone.utc).isoformat(),
            "x-agent-id": "test-agent",
            "x-request-id": "test-req",
            "x-execution-identity": '{"principal": "test-principal"}',
            "x-capability-id": "test-cap",
            "x-target-url": "test-url",
            "x-payment": "BYPASS",
            "x-pgl-pre-cert": "test-cert"
        },
        json={
            "bond_id": str(uuid.uuid4()),
            "challenge_id": str(uuid.uuid4()),
            "pgl_evidence_id": "pgl_123"
        }
    )
    assert response.status_code == 403

def test_authorize_slash_valid_signature(client):
    settings = get_settings()
    secret = getattr(settings, "vnp_cappo_interlink_secret", "dev-interlink-secret")
    timestamp = datetime.now(timezone.utc).isoformat()
    
    mac = hmac.new(
        secret.encode(),
        timestamp.encode(),
        hashlib.sha256
    ).hexdigest()

    bond_id = str(uuid.uuid4())
    response = client.post(
        "/api/internal/interlink/vnp/authorize-slash",
        headers={
            "x-vnp-signature": mac,
            "x-vnp-timestamp": timestamp,
            "x-agent-id": "test-agent",
            "x-request-id": "test-req",
            "x-execution-identity": '{"principal": "test-principal"}',
            "x-capability-id": "test-cap",
            "x-target-url": "test-url",
            "x-payment": "BYPASS",
            "x-pgl-pre-cert": "test-cert"
        },
        json={
            "bond_id": bond_id,
            "challenge_id": str(uuid.uuid4()),
            "pgl_evidence_id": "pgl_456"
        }
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["authorized"] is True
    assert data["action"] == "slash"
    assert "authorization_receipt" in data
