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
        resp = auth_client.post("/v1/exec", json={"prompt": "hi"})
        assert resp.status_code == 401
        assert resp.json()["error"] == "AUTHENTICATION_REQUIRED"

    def test_exec_rejected_with_invalid_key(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/v1/exec", json={"prompt": "hi"}, headers={"X-API-Key": "wrong"}
        )
        assert resp.status_code == 401

    def test_exec_allowed_with_valid_key(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/v1/exec", json={"prompt": "hi"}, headers={"X-API-Key": _KEY}
        )
        assert resp.status_code == 200
        assert resp.json()["response"] == "echo: hi"

    def test_health_is_public(self, auth_client: TestClient) -> None:
        assert auth_client.get("/health").status_code == 200

    def test_admin_route_requires_key(self, auth_client: TestClient) -> None:
        resp = auth_client.put("/v1/kill-switch/ws1", json={"active": True})
        assert resp.status_code == 401


class TestAuthDisabledByDefault:
    def test_exec_open_when_auth_disabled(self, client: TestClient) -> None:
        # Default client fixture uses auth_enabled=False.
        resp = client.post("/v1/exec", json={"prompt": "hi"})
        assert resp.status_code == 200
