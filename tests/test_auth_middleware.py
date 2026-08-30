"""Tests for the global authentication middleware (GAP: middleware wiring).

Proves a request without valid credentials is rejected *before* reaching the
side-effecting ``/v1/exec`` route, that public paths stay open, and that auth is
disabled by default (dev) so it never substitutes for EI authority.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import get_session
from cappo_backend.execution.kms import LocalKMSProvider
from cappo_backend.main import create_app

_KEY = "test-api-key"


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        ei_signing_key="test-signing-key",
        environment="test",
        auth_enabled=True,
        api_keys=_KEY,
        executor_mode="echo",
        runtime_kind="amphoteric",
        runtime_instance="test-runtime",
    )


@pytest.fixture
def auth_client(db: Session, auth_settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=auth_settings)

    def _override_session() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = lambda: auth_settings
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestAuthEnabled:
    def test_exec_rejected_without_key(self, auth_client: TestClient) -> None:
        resp = auth_client.post("/v1/exec", json={"prompt": "hi", "pgl_id": "test-user-id"})
        assert resp.status_code == 401
        assert resp.json()["error"] == "AUTHENTICATION_REQUIRED"

    def test_exec_rejected_with_invalid_key(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/v1/exec", json={"prompt": "hi", "pgl_id": "test-user-id"}, headers={"X-API-Key": "wrong"}
        )
        assert resp.status_code == 401

    def test_exec_returns_workspace_context_missing_without_binding(self, auth_client: TestClient) -> None:
        """A valid API key with no server-side workspace binding now returns WORKSPACE_CONTEXT_MISSING.

        Pre-P0-1: body.workspace_id="default" silently drove execution.
        Post-P0-1: auth_workspace must be present in scope; API keys have no binding yet.
        This test documents the correct behaviour after P0-1.
        """
        resp = auth_client.post(
            "/v1/exec",
            json={"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"},
            headers={"X-API-Key": _KEY},
        )
        assert resp.status_code == 403
        assert resp.json().get("detail", {}).get("error") == "WORKSPACE_CONTEXT_MISSING"

    def test_health_is_public(self, auth_client: TestClient) -> None:
        assert auth_client.get("/health").status_code == 200

    def test_execution_verification_key_is_public(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(LocalKMSProvider, "get_public_key", lambda self, kid: b"k" * 32)
        response = auth_client.get("/api/v1/execution/keys/test-kid")
        assert response.status_code == 200
        assert response.json()["kid"] == "test-kid"
        assert "private_key" not in response.json()

    def test_admin_route_requires_key(self, auth_client: TestClient) -> None:
        resp = auth_client.put("/v1/kill-switch/ws1", json={"active": True})
        assert resp.status_code == 401


class TestAuthDisabledByDefault:
    def test_exec_returns_workspace_context_missing_when_auth_disabled(self, client: TestClient) -> None:
        """Even with auth_enabled=False, auth_workspace must be in scope for exec.

        auth_disabled sets auth_principal="auth-disabled" but still does not
        set auth_workspace. exec_router correctly rejects without workspace context.
        """
        resp = client.post(
            "/v1/exec",
            json={"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"},
            headers={"X-No-Workspace": "true"}
        )
        assert resp.status_code == 403
        assert resp.json().get("detail", {}).get("error") == "WORKSPACE_CONTEXT_MISSING"
