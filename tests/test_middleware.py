"""Integration tests for Auth/Entitlement and PaymentGate middlewares."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.main import app
from cappo_backend.models.governed_run import GovernedRun


def test_public_endpoints_accessible_without_auth() -> None:
    # Use a clean client without the default fixture headers
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_auth_missing_returns_401() -> None:
    client = TestClient(app)
    resp = client.post("/v1/exec", json={"prompt": "hello"})
    assert resp.status_code == 401
    assert "credentials are required" in resp.json()["detail"]


def test_auth_invalid_returns_403() -> None:
    client = TestClient(app)
    resp = client.post(
        "/v1/exec",
        json={"prompt": "hello"},
        headers={"X-API-Key": "invalid-key"},
    )
    assert resp.status_code == 403
    assert "Invalid authentication" in resp.json()["detail"]


def test_unlicensed_returns_403() -> None:
    client = TestClient(app)
    resp = client.post(
        "/v1/exec",
        json={"prompt": "hello"},
        headers={"X-API-Key": "unlicensed-key"},
    )
    assert resp.status_code == 403
    assert "License key is invalid" in resp.json()["detail"]


def test_path_traversal_blocked() -> None:
    client = TestClient(app)
    resp = client.get("/v1//exec")
    assert resp.status_code == 400
    assert "Malicious path traversal" in resp.json()["detail"]



def test_middleware_order_kill_switch_precedence(client: TestClient, db: Session) -> None:
    # 1. Enable kill-switch
    client.put("/v1/kill-switch/default", json={"active": True})

    # 2. A request with a valid API key (supplied by the client fixture) but active kill switch
    # should fail with 402 at the middleware level before entering the orchestrator.
    resp = client.post("/v1/exec", json={"prompt": "hello"})
    assert resp.status_code == 402
    assert resp.json()["detail"]["error"] == "PAYMENT_REQUIRED"
    assert resp.json()["detail"]["reason"] == "kill_switch"

    # Confirm no governed run was ever compiled or created
    assert db.query(GovernedRun).count() == 0
