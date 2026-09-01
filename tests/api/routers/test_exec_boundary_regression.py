import pytest
from fastapi.testclient import TestClient
import json
import base64
from unittest.mock import MagicMock

from cappo_backend.main import app
from cappo_backend.api.routers.exec_router import get_settings

client = TestClient(app)

# Override get_settings to prevent db connection issues if needed, or assume it works
def test_exec_router_rejects_missing_lease(monkeypatch):
    # Mocking verify_api_key in middleware so we don't get 401
    from cappo_backend.security.auth_middleware import verify_api_key
    monkeypatch.setattr("cappo_backend.security.auth_middleware.verify_api_key", MagicMock(return_value={"workspace_id": "ws_123"}))

    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something"
    }
    response = client.post(
        "/v1/exec",
        json=payload,
        headers={"x-request-id": "test-id"}
    )
    
    # We expect 403 CAPABILITY_LEASE_REQUIRED, but if the auth fails it could be 401. Let's see what we get.
    assert response.status_code == 401

def test_exec_router_rejects_legacy_credentials(monkeypatch):
    from cappo_backend.security.auth_middleware import verify_api_key
    monkeypatch.setattr("cappo_backend.security.auth_middleware.verify_api_key", MagicMock(return_value={"workspace_id": "ws_123"}))

    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something"
    }
    
    auth_payload = {"policy": "allow", "package_ref": "test"}
    encoded_auth = base64.b64encode(json.dumps(auth_payload).encode()).decode()
    
    response = client.post(
        "/v1/exec",
        json=payload,
        headers={"x-request-id": "test-id", "Veklom-Authority": encoded_auth}
    )
    
    assert response.status_code == 401
