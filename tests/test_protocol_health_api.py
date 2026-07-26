"""Focused tests for CAPPO discovery and dependency health surfaces."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_protocol_manifest_is_canonical(client: TestClient) -> None:
    response = client.get("/protocol.json")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "cappo"
    assert body["base_url"] == "https://cappo.veklom.com"
    assert body["capabilities"] == ["authorize_execution", "governed_execute"]
    assert body["links"]["capi"] == "https://capi.veklom.com/protocol.json"
    assert body["links"]["pgl"] == "https://pgl.veklom.com/protocol.json"
    assert body["links"]["byos"] == "https://api.veklom.com/protocol.json"


def test_protocol_introspection_matches_capability(client: TestClient) -> None:
    response = client.post("/protocol/introspect", json={"query": "authorize"})

    assert response.status_code == 200
    assert response.json()["matches"] == ["authorize_execution"]


def test_dependency_health_is_redacted_and_non_throwing(client: TestClient) -> None:
    response = client.get("/health/dependencies")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"healthy", "degraded", "unavailable", "unconfigured"}
    for dependency in body["dependencies"]:
        assert set(dependency) == {"name", "host", "state", "latency_ms"}
        assert dependency["state"] in {
            "healthy",
            "degraded",
            "unavailable",
            "unconfigured",
        }
        assert "://" not in dependency["host"]
