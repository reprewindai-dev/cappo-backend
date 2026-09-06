import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import get_session
from cappo_backend.main import app


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

def test_exec_router_rejects_missing_auth_principal(monkeypatch):
    async def mock_dispatch(self, request: Request, call_next) -> Response:
        request.scope["auth_workspace"] = "ws_123"
        return await call_next(request)
    monkeypatch.setattr("cappo_backend.security.auth_middleware.AuthMiddleware.dispatch", mock_dispatch)
    
    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {},
        "capability_lease": {
            "mount_id": "m1",
            "token_id": "t1",
            "nonce": "n1",
            "execution_id": "e1"
        }
    }
    response = client.post("/v1/exec", json=payload, headers={"x-request-id": "test-id", "X-Veklom-Capi-Key": "test-key"})
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "AUTHENTICATION_REQUIRED"

def test_exec_router_rejects_missing_workspace_context(monkeypatch):
    async def mock_dispatch(self, request: Request, call_next) -> Response:
        request.scope["auth_principal"] = "test-principal"
        if "auth_workspace" in request.scope:
            del request.scope["auth_workspace"]
        return await call_next(request)
    monkeypatch.setattr("cappo_backend.security.auth_middleware.AuthMiddleware.dispatch", mock_dispatch)
    
    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {},
        "capability_lease": {
            "mount_id": "m1",
            "token_id": "t1",
            "nonce": "n1",
            "execution_id": "e1"
        }
    }
    response = client.post("/v1/exec", json=payload, headers={"x-request-id": "test-id", "X-Veklom-Capi-Key": "test-key"})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "WORKSPACE_CONTEXT_MISSING"

def test_exec_router_rejects_workspace_mismatch(monkeypatch):
    async def mock_dispatch(self, request: Request, call_next) -> Response:
        request.scope["auth_principal"] = "test-principal"
        request.scope["auth_workspace"] = "ws_123"
        return await call_next(request)
    monkeypatch.setattr("cappo_backend.security.auth_middleware.AuthMiddleware.dispatch", mock_dispatch)
    
    payload = {
        "action": "record.create",
        "workspace_id": "different_workspace",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {},
        "capability_lease": {
            "mount_id": "m1",
            "token_id": "t1",
            "nonce": "n1",
            "execution_id": "e1"
        }
    }
    response = client.post("/v1/exec", json=payload, headers={"x-request-id": "test-id", "X-Veklom-Capi-Key": "test-key"})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "WORKSPACE_MISMATCH"

def test_exec_router_activation_lease_required(monkeypatch):
    async def mock_dispatch(self, request: Request, call_next) -> Response:
        request.scope["auth_principal"] = "test-principal"
        request.scope["auth_workspace"] = "ws_123"
        return await call_next(request)
    monkeypatch.setattr("cappo_backend.security.auth_middleware.AuthMiddleware.dispatch", mock_dispatch)
    
    payload = {
        "action": "activation.write",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {}
    }
    response = client.post("/v1/exec", json=payload, headers={"x-request-id": "test-id", "X-Veklom-Capi-Key": "test-key"})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "ACTIVATION_CAPABILITY_LEASE_REQUIRED"

def test_exec_router_rejects_workspace_scope_mismatch(monkeypatch):
    async def mock_dispatch(self, request: Request, call_next) -> Response:
        request.scope["auth_principal"] = "test-principal"
        request.scope["auth_workspace"] = "ws_123"
        return await call_next(request)
    monkeypatch.setattr("cappo_backend.security.auth_middleware.AuthMiddleware.dispatch", mock_dispatch)
    
    from unittest.mock import MagicMock
    mock_registry = MagicMock()
    mock_record = MagicMock()
    mock_record.token.scope.workspace = "wrong_workspace"
    mock_record.mount.scope.workspace = "wrong_workspace"
    mock_registry.status.return_value = (mock_record, "mounted")
    monkeypatch.setattr("cappo_backend.api.routers.exec_router.get_registry", lambda r, d: mock_registry)
    
    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {},
        "capability_lease": {
            "mount_id": "m1",
            "token_id": "t1",
            "nonce": "n1",
            "execution_id": "e1"
        }
    }
    response = client.post("/v1/exec", json=payload, headers={"x-request-id": "test-id", "X-Veklom-Capi-Key": "test-key"})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "WORKSPACE_SCOPE_MISMATCH"

def test_exec_router_rejects_lease_not_active(monkeypatch):
    async def mock_dispatch(self, request: Request, call_next) -> Response:
        request.scope["auth_principal"] = "test-principal"
        request.scope["auth_workspace"] = "ws_123"
        return await call_next(request)
    monkeypatch.setattr("cappo_backend.security.auth_middleware.AuthMiddleware.dispatch", mock_dispatch)
    
    from unittest.mock import MagicMock
    mock_registry = MagicMock()
    mock_registry.status.return_value = (None, "not_found")
    monkeypatch.setattr("cappo_backend.api.routers.exec_router.get_registry", lambda r, d: mock_registry)
    
    payload = {
        "action": "record.create",
        "action_cost_cents": 1,
        "prompt": "do something",
        "security": {},
        "capability_lease": {
            "mount_id": "m1",
            "token_id": "t1",
            "nonce": "n1",
            "execution_id": "e1"
        }
    }
    response = client.post("/v1/exec", json=payload, headers={"x-request-id": "test-id", "X-Veklom-Capi-Key": "test-key"})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "CAPABILITY_LEASE_NOT_ACTIVE"

