"""CAPPO dependency health probes."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import redis
from fastapi import APIRouter, Depends
from sqlalchemy import text

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import engine

router = APIRouter(tags=["health"])

@router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/runtime/identity")
def runtime_identity(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    # Need to import os and subprocess if not already, or we can just fetch from env vars
    import os
    import subprocess
    import time
    
    sha = os.environ.get("SOURCE_COMMIT_SHA", "NOT_VERIFIED")
    if sha == "NOT_VERIFIED":
        try:
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()
        except Exception:
            pass

    return {
        "service": "cappo-backend",
        "environment": os.environ.get("ENVIRONMENT", "production"),
        "source_commit_sha": sha,
        "build_timestamp": os.environ.get("BUILD_TIMESTAMP", str(int(time.time()))),
        "artifact_digest": os.environ.get("ARTIFACT_DIGEST", "N/A")
    }

_PROBE_TIMEOUT_SECONDS = 2.0
_WORST_STATE = {"healthy": 0, "degraded": 1, "unconfigured": 1, "unavailable": 2}
_DATABASE_PROBE_LOCK = asyncio.Lock()


def _host(url: str) -> str:
    parsed = urlparse(url)
    return parsed.hostname or "unknown"


def _result(name: str, host: str, state: str, started: float) -> dict[str, Any]:
    return {
        "name": name,
        "host": host,
        "state": state,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _http_probe_paths(name: str, base_url: str) -> tuple[str, ...]:
    parsed_path = urlparse(base_url).path.rstrip("/")
    if name == "executor" and parsed_path.endswith("/v1"):
        return ("/models",)
    return ("/health", "/protocol.json")


async def _probe_http(name: str, base_url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = None
            for path in _http_probe_paths(name, base_url):
                response = await client.get(f"{base_url.rstrip('/')}{path}")
                if response.status_code != 404:
                    break
            assert response is not None
            state = "healthy" if 200 <= response.status_code < 300 else "degraded"
    except Exception:
        state = "unavailable"
    return _result(name, _host(base_url), state, started)


async def _probe_redis(url: str) -> dict[str, Any]:
    started = time.perf_counter()

    def check() -> bool:
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=_PROBE_TIMEOUT_SECONDS,
            socket_timeout=_PROBE_TIMEOUT_SECONDS,
        )
        return bool(client.ping())

    try:
        available = await asyncio.wait_for(
            asyncio.to_thread(check), timeout=_PROBE_TIMEOUT_SECONDS
        )
        state = "healthy" if available else "unavailable"
    except Exception:
        state = "unavailable"
    return _result("redis", _host(url), state, started)


async def _probe_database() -> dict[str, Any]:
    started = time.perf_counter()

    def check() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    if _DATABASE_PROBE_LOCK.locked():
        return _result("database", "configured", "unavailable", started)

    try:
        async with _DATABASE_PROBE_LOCK:
            await asyncio.wait_for(asyncio.to_thread(check), timeout=_PROBE_TIMEOUT_SECONDS)
        state = "healthy"
    except Exception:
        state = "unavailable"
    return _result("database", "configured", state, started)


def _unconfigured(name: str) -> dict[str, Any]:
    return {"name": name, "host": "unconfigured", "state": "unconfigured", "latency_ms": 0.0}


async def _completed(result: dict[str, Any]) -> dict[str, Any]:
    return result


@router.get("/health/dependencies")
async def health_dependencies(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    configured = [
        ("pgl", settings.pgl_ledger_url),
        ("byos", settings.veklom_byos_backend_url),
    ]
    if settings.executor_mode.lower() != "echo":
        configured.append(("executor", settings.llm_base_url))
    probes = [_probe_database()]
    probes.extend(
        _probe_http(name, url) if url else _completed(_unconfigured(name))
        for name, url in configured
    )
    if settings.cache_warm_backend.lower() == "redis":
        probes.append(
            _probe_redis(settings.redis_url)
            if settings.redis_url
            else _completed(_unconfigured("redis"))
        )
    checks = list(await asyncio.gather(*probes))

    overall = max(checks, key=lambda check: _WORST_STATE[check["state"]])["state"]
    return {"status": overall, "dependencies": checks}
