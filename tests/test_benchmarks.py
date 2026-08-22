from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.models.governed_run import GovernedRun


def test_leaderboard_endpoint_returns_empty_without_runs(client: TestClient) -> None:
    resp = client.get("/api/v1/benchmarks/leaderboard")
    assert resp.status_code == 200
    assert resp.json() == []


def test_leaderboard_endpoint_uses_recorded_run_data(
    client: TestClient, db: Session
) -> None:
    db.add_all(
        [
            GovernedRun(
                workspace_id="test-workspace",
                tenant_id="test-workspace",
                state="executed",
                request_payload={},
                result_payload={"provider": "recorded-provider", "latency_ms": 120},
            ),
            GovernedRun(
                workspace_id="test-workspace",
                tenant_id="test-workspace",
                state="failed",
                request_payload={},
                result_payload={"provider": "recorded-provider", "latency_ms": 240},
            ),
        ]
    )
    db.commit()

    resp = client.get("/api/v1/benchmarks/leaderboard")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": "recorded-provider",
            "name": "recorded-provider",
            "provider": "recorded-provider",
            "p50": None,
            "p95": None,
            "p99": None,
            "successRatePercent": 50.0,
            "measuredFrom": "governed_runs",
            "sampleCount": 2,
        }
    ]


def test_leaderboard_percentiles_require_twenty_samples(
    client: TestClient, db: Session
) -> None:
    db.add_all(
        [
            GovernedRun(
                workspace_id="test-workspace",
                tenant_id="test-workspace",
                state="executed",
                request_payload={},
                result_payload={"provider": "well-sampled", "latency_ms": 120},
            )
            for _ in range(20)
        ]
    )
    db.commit()

    resp = client.get("/api/v1/benchmarks/leaderboard")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": "well-sampled",
            "name": "well-sampled",
            "provider": "well-sampled",
            "p50": 120.0,
            "p95": 120.0,
            "p99": 120.0,
            "successRatePercent": 100.0,
            "measuredFrom": "governed_runs",
            "sampleCount": 20,
        }
    ]


def test_staking_markets_endpoint(client: TestClient) -> None:
    resp = client.get("/api/v1/benchmarks/staking/markets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 3
    first_item = data[0]
    # Check StakingMarket fields
    assert "id" in first_item
    assert "title" in first_item
    assert "category" in first_item
    assert "yesPrice" in first_item
    assert "noPrice" in first_item
    assert "volume" in first_item
    assert "poolYes" in first_item
    assert "poolNo" in first_item
    assert "resolutionDate" in first_item
    assert "targetApi" in first_item
    assert "resolved" in first_item


def test_logs_endpoint(client: TestClient) -> None:
    resp = client.get("/api/v1/benchmarks/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 0
    if len(data) > 0:
        first_item = data[0]
        # Check ProbeLog fields
        assert "id" in first_item
        assert "timestamp" in first_item
        assert "source" in first_item
        assert "type" in first_item
        assert "message" in first_item


def test_compile_endpoint(client: TestClient) -> None:
    body = {
        "codeText": "API Name: Test API\nGET /test - returns status ok",
        "apiName": "Test API",
        "category": "Testing"
    }
    resp = client.post("/api/v1/benchmarks/compile", json=body)
    assert resp.status_code == 200
    data = resp.json()
    # Check CompileResult fields
    assert data["apiName"] == "Test API"
    assert data["category"] == "Testing"
    assert data["version"] == "1.0.0"
    assert "restEndpoint" in data
    assert data["schemaType"] == "MCP+REST Schema"
    assert "mcpToolDefinition" in data
    assert "syntheticVerificationResult" in data
    
    verify_result = data["syntheticVerificationResult"]
    assert "latencyMs" in verify_result
    assert "driftScore" in verify_result
    assert "uniquenessFactor" in verify_result
    assert "comprehensionScore" in verify_result
    assert "aiFeedback" in verify_result
