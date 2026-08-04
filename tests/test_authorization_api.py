"""Focused tests for side-effect-free authorization."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_authorize_allow_directive_passes_governance_gate(client: TestClient) -> None:
    response = client.post(
        "/api/v1/execution/authorize",
        json={"agent_id": "agent-1", "capability_id": "exec", "directive": "ALLOW"},
    )

    assert response.status_code == 200
    body = response.json()
    # Temporal policy is resolved internally by the current MCP v2 stack.
    assert body["decision"] in {"APPROVED", "NEEDS_APPROVAL"}
    assert body["decision"] != "REJECTED"
    assert body["lane"] == "standard"
    assert body["authorization_id"].startswith("auth_")
    assert len(body["decision_hash"]) == 64


def test_authorize_missing_directive_requires_approval(client: TestClient) -> None:
    response = client.post(
        "/api/v1/execution/authorize",
        json={"agent_id": "agent-1", "capability_id": "exec"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "NEEDS_APPROVAL"
    assert body["decision"] != "APPROVED"


def test_authorize_does_not_execute(client: TestClient, monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("authorization must not invoke an executor")

    monkeypatch.setattr("cappo_backend.api.routers.exec_router.build_executor", fail_if_called)
    response = client.post(
        "/api/v1/execution/authorize",
        json={"agent_id": "agent-1", "directive": "ALLOW"},
    )

    assert response.status_code == 200
    assert response.json()["decision"] in {"APPROVED", "NEEDS_APPROVAL"}
