"""Focused tests for CAPPO discovery and dependency health surfaces."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cappo_backend.api.routers import health_router


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


def test_dependency_probes_start_concurrently(monkeypatch) -> None:
    async def exercise() -> None:
        started = 0
        all_started = asyncio.Event()
        release = asyncio.Event()

        async def probe(name: str) -> dict[str, object]:
            nonlocal started
            started += 1
            if started == 3:
                all_started.set()
            await release.wait()
            return {"name": name, "host": "configured", "state": "healthy", "latency_ms": 0.0}

        async def fake_database() -> dict[str, object]:
            return await probe("database")

        async def fake_http(name: str, _url: str) -> dict[str, object]:
            return await probe(name)

        monkeypatch.setattr(health_router, "_probe_database", fake_database)
        monkeypatch.setattr(health_router, "_probe_http", fake_http)
        settings = SimpleNamespace(
            pgl_ledger_url="http://pgl",
            veklom_byos_backend_url="http://byos",
            executor_mode="echo",
            llm_base_url="",
            cache_warm_backend="memory",
            redis_url="",
        )

        request = asyncio.create_task(health_router.health_dependencies(settings))
        await asyncio.wait_for(all_started.wait(), timeout=0.5)
        release.set()
        result = await asyncio.wait_for(request, timeout=0.5)

        assert [item["name"] for item in result["dependencies"]] == ["database", "pgl", "byos"]

    asyncio.run(exercise())
