"""Focused tests for side-effect-free authorization."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cappo_backend.services.authorization import normalize_directive


def test_authorize_approved_path(client: TestClient) -> None:
    response = client.post(
        "/api/v1/execution/authorize",
        json={"agent_id": "agent-1", "capability_id": "exec", "directive": "ALLOW"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "APPROVED"
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
    assert response.json()["decision"] == "APPROVED"


def test_authorize_rejects_safety_denial(client: TestClient, monkeypatch) -> None:
    class DenyingStack:
        def pre_execution_assessment(self, *_args, **_kwargs):
            return {
                "allow": False,
                "governance": {
                    "is_valid": True,
                    "policy_allows": True,
                    "requires_approval": False,
                },
            }

    monkeypatch.setattr(
        "cappo_backend.services.authorization.get_mcp_v2_stack",
        lambda: DenyingStack(),
    )
    response = client.post(
        "/api/v1/execution/authorize",
        json={"agent_id": "agent-1", "directive": "ALLOW"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "REJECTED"
    assert body["reason"] == "safety assessment denied the request"


def test_blank_risk_tier_preserves_standard_default() -> None:
    normalized = normalize_directive({"directive": "ALLOW", "risk_tier": ""}, strict=False)

    assert normalized.risk_tier == "standard"


def test_midnight_policy_time_is_preserved(client: TestClient, monkeypatch) -> None:
    observed = {}

    class CapturingStack:
        def pre_execution_assessment(self, *_args, **kwargs):
            observed["hour"] = kwargs["at"].hour
            return {
                "allow": True,
                "governance": {
                    "is_valid": True,
                    "policy_allows": True,
                    "requires_approval": False,
                },
            }

    monkeypatch.setattr(
        "cappo_backend.services.authorization.get_mcp_v2_stack",
        lambda: CapturingStack(),
    )
    response = client.post(
        "/api/v1/execution/authorize",
        json={"agent_id": "agent-1", "directive": "ALLOW", "time_of_day": 0},
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "APPROVED"
    assert observed["hour"] == 0
