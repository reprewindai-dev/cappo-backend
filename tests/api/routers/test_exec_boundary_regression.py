import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
import json
import base64
import time
from cappo_backend.main import app
from starlette.requests import Request
from starlette.responses import Response
from cappo_backend.db.session import get_session
from cappo_backend.config import get_settings, Settings

def override_get_session():
    yield MagicMock()

def override_get_settings():
    return Settings(
        environment="test",
        executor_mode="ollama",
        ollama_keep_alive=300,
        capi_gatekeeper_public_key="test-key"
    )

app.dependency_overrides[get_session] = override_get_session
app.dependency_overrides[get_settings] = override_get_settings

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_payment_gate():
    with patch("cappo_backend.api.routers.exec_router._check_payment") as mock:
        yield mock

@pytest.fixture(autouse=True)
def mock_wid_middleware():
    with patch("cappo_backend.identity.middleware.WIDMiddlewareContext.enforce") as mock:
        yield mock

@pytest.fixture(autouse=True)
def mock_capi_pipeline():
    with patch("cappo_backend.core.capi_pipeline.enforce_capi_pipeline", new_callable=AsyncMock) as mock:
        mock.return_value = {"evidence_id": "mock"}
        yield mock

@pytest.fixture(autouse=True)
def mock_verify_exec():
    with patch("cappo_backend.api.routers.exec_router._verify_exec_request_integrity", new_callable=AsyncMock) as mock:
        yield mock

@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    async def mock_dispatch(self, request: Request, call_next) -> Response:
        request.scope["auth_workspace"] = "ws_123"
        return await call_next(request)
    
    with patch("cappo_backend.security.auth_middleware.AuthMiddleware.dispatch", new=mock_dispatch):
        yield

def test_exec_router_rejects_missing_lease():
    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {}
    }
    response = client.post(
        "/v1/exec",
        json=payload,
        headers={"x-request-id": "test-id", "X-Veklom-Capi-Key": "test-key"}
    )
    
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "CAPABILITY_LEASE_REQUIRED"

def test_exec_router_rejects_legacy_credentials():
    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {}
    }
    
    auth_payload = {
        "authority_id": "test",
        "ephemeral_execution_id": "test",
        "scope_hash": "test",
        "policy_decision_hash": "test",
        "candidate_act_hash": "test",
        "destination_hash": "test",
        "rights": ["test"],
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 300,
        "proof_of_possession": "test"
    }
    encoded_auth = base64.b64encode(json.dumps(auth_payload).encode()).decode()
    
    response = client.post(
        "/v1/exec",
        json=payload,
        headers={
            "x-request-id": "test-id", 
            "Veklom-Authority": encoded_auth,
            "X-Veklom-Capi-Key": "test-key"
        }
    )
    
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "LEGACY_CREDENTIALS_UNSUPPORTED"
