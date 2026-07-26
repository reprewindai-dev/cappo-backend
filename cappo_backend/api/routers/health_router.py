"""CAPPO dependency health probes."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import engine

router = APIRouter(tags=["health"])
_PROBE_TIMEOUT_SECONDS = 2.0
_WORST_STATE = {"healthy": 0, "degraded": 1, "unconfigured": 1, "unavailable": 2}


def _host(url: str) -> str:
    parsed = urlparse(url)
    return parsed.hostname or "unknown"


async def _probe_http(name: str, base_url: str) -> dict[str, Any]:
    started = time.perf_counter()
    url = f"{base_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.get(url)
            if response.status_code == 404:
                response = await client.get(f"{base_url.rstrip('/')}/protocol.json")
            state = "healthy" if 200 <= response.status_code < 300 else "degraded"
        return {
            "name": name,
            "host": _host(base_url),
            "state": state,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception:
        return {
            "name": name,
            "host": _host(base_url),
            "state": "unavailable",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }


async def _probe_database() -> dict[str, Any]:
    started = time.perf_counter()

    def check() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(asyncio.to_thread(check), timeout=_PROBE_TIMEOUT_SECONDS)
        state = "healthy"
    except Exception:
        state = "unavailable"
    return {
        "name": "database",
        "host": "configured",
        "state": state,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _unconfigured(name: str) -> dict[str, Any]:
    return {"name": name, "host": "unconfigured", "state": "unconfigured", "latency_ms": 0.0}


@router.get("/health/dependencies")
async def health_dependencies(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [await _probe_database()]
    configured = [
        ("pgl", settings.pgl_ledger_url),
        ("byos", settings.veklom_byos_backend_url),
    ]
    if settings.executor_mode.lower() != "echo":
        configured.append(("executor", settings.llm_base_url))
    if settings.cache_warm_backend.lower() == "redis":
        configured.append(("redis", settings.redis_url or None))
    for name, url in configured:
        checks.append(await _probe_http(name, url) if url else _unconfigured(name))
    overall = max(checks, key=lambda check: _WORST_STATE[check["state"]])["state"]
    return {"status": overall, "dependencies": checks}
