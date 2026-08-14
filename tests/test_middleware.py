"""Integration tests for Auth/Entitlement and PaymentGate middlewares."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.config import get_settings as _get_settings
from cappo_backend.db.session import get_session
from cappo_backend.main import app, create_app
from cappo_backend.models.governed_run import GovernedRun

_KEY = "test-mw-key"


@pytest.fixture
def auth_entitlement_client(db: Session) -> Iterator[TestClient]:
    """Client wired to a create_app instance with auth enabled, using in-memory DB."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        ei_signing_key="test-signing-key",
        environment="test",
        auth_enabled=True,
        api_keys=_KEY,
        executor_mode="echo",
        runtime_kind="amphoteric",
        runtime_instance="test-runtime",
    )
    test_app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        yield db

    test_app.dependency_overrides[get_session] = _override_session
    test_app.dependency_overrides[_get_settings] = lambda: settings
    yield TestClient(test_app)
    test_app.dependency_overrides.clear()


def test_public_endpoints_accessible_without_auth() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_auth_missing_returns_401(auth_entitlement_client: TestClient) -> None:
    resp = auth_entitlement_client.post("/v1/exec", json={"prompt": "hello"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "AUTHENTICATION_REQUIRED"


def test_auth_invalid_returns_401(auth_entitlement_client: TestClient) -> None:
    resp = auth_entitlement_client.post(
        "/v1/exec",
        json={"prompt": "hello"},
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "AUTHENTICATION_REQUIRED"


def test_valid_key_reaches_exec(auth_entitlement_client: TestClient) -> None:
    resp = auth_entitlement_client.post(
        "/v1/exec",
        json={"prompt": "hello", "directive": "ALLOW"},
        headers={"X-API-Key": _KEY},
    )
    assert resp.status_code == 200


def test_exec_open_when_auth_disabled(client: TestClient) -> None:
    resp = client.post("/v1/exec", json={"prompt": "hello", "directive": "ALLOW"})
    assert resp.status_code == 200


def test_middleware_order_kill_switch_precedence(client: TestClient, db: Session) -> None:
    client.put("/v1/kill-switch/default", json={"active": True})

    resp = client.post("/v1/exec", json={"prompt": "hello"})
    assert resp.status_code == 402
    assert resp.json()["detail"]["error"] == "PAYMENT_REQUIRED"
    assert resp.json()["detail"]["reason"] == "kill_switch"

    assert db.query(GovernedRun).count() == 0
