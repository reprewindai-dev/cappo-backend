
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

