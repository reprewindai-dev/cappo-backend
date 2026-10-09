"""Integration tests for the MCPAPI v2.0 governance facade + API router."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from cappo_backend.config import Settings
from cappo_backend.main import create_app
from cappo_backend.services.mcp_v2 import get_mcp_v2_stack, reset_mcp_v2_stack
from cappo_backend.services.safety import CurrentMetric, Observation


@pytest.fixture(autouse=True)
def _reset_stack() -> None:
    reset_mcp_v2_stack()
    yield
    reset_mcp_v2_stack()


@pytest.fixture
def client() -> TestClient:
    settings = Settings(api_keys="test-key", environment="development")
    return TestClient(create_app(settings))


def _headers() -> dict[str, str]:
    return {"X-API-Key": "test-key"}


def test_assess_clean_request_allowed(client: TestClient) -> None:
    resp = client.post(
        "/v1/governance/v2/assess",
        headers=_headers(),
        json={"agent_id": "a1", "trust_score": 95, "requests_per_hour": 10, "time_of_day": 12},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allow"] is True
    assert body["intelligence"]["threat_level"] == "green"
    assert "evidence_hash" in body


def test_assess_records_risk_profile(client: TestClient) -> None:
    client.post(
        "/v1/governance/v2/assess",
        headers=_headers(),
        json={"agent_id": "a2", "trust_score": 20, "time_of_day": 12},
    )
    resp = client.get("/v1/governance/v2/risk/a2", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["assessed"] is True
    assert body["overall_risk_score"] > 0


def test_risk_profile_unknown_agent(client: TestClient) -> None:
    resp = client.get("/v1/governance/v2/risk/nobody", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["assessed"] is False


def test_quarantine_flow_via_facade_and_api(client: TestClient) -> None:
    # Seed a confident baseline directly on the shared stack, then trigger a spike.
    stack = get_mcp_v2_stack()
    pattern = [9, 10, 11]
    for i in range(60):
        stack.baselines.record_observation(
            "spiker",
            Observation(
                timestamp=datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),
                requests_in_window=pattern[i % 3],
                failure_rate=0.05,
                capabilities_used=("search",),
            ),
        )
    stack.baselines.build_baseline("spiker")

    # With auth disabled the middleware binds the caller to "default-local-workspace";
    # a quarantined request is only visible through the queue of its own workspace.
    evidence = stack.pre_execution_assessment(
        "spiker",
        {"agent_id": "spiker"},
        metric=CurrentMetric(requests_per_hour=900, failure_rate=0.05, time_of_day=12),
        trust_score=70,
        workspace_id="default-local-workspace",
    )
    assert evidence["safety"]["quarantine_id"] is not None

    queue = client.get("/v1/governance/v2/quarantine", headers=_headers()).json()
    assert queue["workspace_id"] == "default-local-workspace"
    assert len(queue["items"]) == 1
    qid = queue["items"][0]["quarantine_id"]
    assert queue["items"][0]["requester_id"] == "spiker"

    # Trust is never accepted from the request: the old body shape is a schema error.
    self_declared = client.post(
        f"/v1/governance/v2/quarantine/{qid}/approve",
        headers=_headers(),
        json={"approver_id": "weak", "approver_trust": 100},
    )
    assert self_declared.status_code == 422

    # The caller is not in the server-held approver registry: refused.
    low = client.post(f"/v1/governance/v2/quarantine/{qid}/approve", headers=_headers(), json={})
    assert low.status_code == 403
    assert "APPROVER_NOT_AUTHORIZED" in low.json()["detail"]

    # Deny needs the same authority (or the workspace owner's session): refused too.
    denied = client.post(
        f"/v1/governance/v2/quarantine/{qid}/deny",
        headers=_headers(),
        json={"reason": "operator rejected"},
    )
    assert denied.status_code == 403
    assert "DENY_NOT_AUTHORIZED" in denied.json()["detail"]
    assert stack.quarantine.get(qid).status == "quarantined"


def test_approve_missing_quarantine_404(client: TestClient) -> None:
    resp = client.post(
        "/v1/governance/v2/quarantine/does-not-exist/approve",
        headers=_headers(),
        json={},
    )
    assert resp.status_code == 404
