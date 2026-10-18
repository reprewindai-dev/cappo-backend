import asyncio
import time
from typing import Any, Dict
from urllib.parse import urlparse

import httpx
import redis
from fastapi import APIRouter, Depends
from sqlalchemy import text

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import engine

router = APIRouter(tags=["observation"])

_PROBE_TIMEOUT_SECONDS = 2.0
_DATABASE_PROBE_LOCK = asyncio.Lock()
_FRESHNESS_SECONDS = 60

def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _timestamp_plus(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

def _sanitize_endpoint(name: str) -> str:
    return name

def _build_observation(
    service: str,
    status: str,
    latency_ms: float = 0.0,
    http_class: str = "N/A",
    failure_reason: str = "none"
) -> Dict[str, Any]:
    return {
        "service": service,
        "observed_at": _timestamp(),
        "expires_at": _timestamp_plus(_FRESHNESS_SECONDS),
        "http_class": http_class,
        "latency_ms": latency_ms,
        "failure_reason": failure_reason,
        "status": status
    }

def _unknown(service: str, reason: str = "unconfigured") -> Dict[str, Any]:
    return _build_observation(service, "UNKNOWN_NOT_OBSERVED", failure_reason=reason)

async def _probe_http(service: str, url: str) -> Dict[str, Any]:
    if not url:
        return _unknown(service)
    
    started = time.perf_counter()
    
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            path = "/" if service == "Ollama Inference Nodes" else "/health"
            
            resp = await client.get(f"{base_url}{path}")
            latency = round((time.perf_counter() - started) * 1000, 2)
            http_class = f"{resp.status_code}"
            
            if 200 <= resp.status_code < 300:
                return _build_observation(service, "OBSERVED_HEALTHY", latency, http_class)
            elif resp.status_code >= 500:
                return _build_observation(service, "OBSERVED_UNAVAILABLE", latency, http_class, "server_error")
            else:
                return _build_observation(service, "OBSERVED_DEGRADED", latency, http_class, "unexpected_status")
    except asyncio.TimeoutError:
        return _build_observation(service, "UNKNOWN_NOT_OBSERVED", 0, "TIMEOUT", "connection_timeout")
    except Exception as e:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation(service, "OBSERVED_UNAVAILABLE", latency, "CONNECTION_ERROR", type(e).__name__)

async def _probe_redis(url: str) -> Dict[str, Any]:
    if not url:
        return _unknown("Redis Ephemeral Cache")
    
    started = time.perf_counter()
    def check() -> bool:
        client = redis.Redis.from_url(url, socket_connect_timeout=_PROBE_TIMEOUT_SECONDS, socket_timeout=_PROBE_TIMEOUT_SECONDS)
        return bool(client.ping())

    try:
        available = await asyncio.wait_for(asyncio.to_thread(check), timeout=_PROBE_TIMEOUT_SECONDS)
        latency = round((time.perf_counter() - started) * 1000, 2)
        status = "OBSERVED_HEALTHY" if available else "OBSERVED_UNAVAILABLE"
        return _build_observation("Redis Ephemeral Cache", status, latency, "PING_OK" if available else "PING_FAIL", "none" if available else "ping_failed")
    except asyncio.TimeoutError:
        return _build_observation("Redis Ephemeral Cache", "UNKNOWN_NOT_OBSERVED", 0, "TIMEOUT", "connection_timeout")
    except Exception as e:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation("Redis Ephemeral Cache", "OBSERVED_UNAVAILABLE", latency, "CONNECTION_ERROR", type(e).__name__)

async def _probe_database() -> Dict[str, Any]:
    started = time.perf_counter()
    def check() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    if _DATABASE_PROBE_LOCK.locked():
        return _build_observation("PostgreSQL Shared State", "UNKNOWN_NOT_OBSERVED", 0, "LOCK_CONTENTION", "probe_lock_busy")

    try:
        async with _DATABASE_PROBE_LOCK:
            await asyncio.wait_for(asyncio.to_thread(check), timeout=_PROBE_TIMEOUT_SECONDS)
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation("PostgreSQL Shared State", "OBSERVED_HEALTHY", latency, "SQL_OK")
    except asyncio.TimeoutError:
        return _build_observation("PostgreSQL Shared State", "UNKNOWN_NOT_OBSERVED", 0, "TIMEOUT", "connection_timeout")
    except Exception as e:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation("PostgreSQL Shared State", "OBSERVED_UNAVAILABLE", latency, "CONNECTION_ERROR", type(e).__name__)

@router.get("/runtime/observations")
async def get_observations(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    probes = [
        _probe_http("Capability Mount API (CAPPO)", "http://127.0.0.1:8002/health"),
        _probe_http("Proof-of-Graph Ledger (PGL)", settings.pgl_ledger_url),
        _probe_http("Veklom BYOS Backend", settings.veklom_byos_backend_url),
        _probe_database()
    ]
    
    if settings.executor_mode.lower() != "echo" and settings.llm_base_url:
        probes.append(_probe_http("Ollama Inference Nodes", settings.llm_base_url))
    else:
        probes.append(asyncio.sleep(0, result=_unknown("Ollama Inference Nodes", "disabled_in_config")))
        
    if settings.cache_warm_backend.lower() == "redis":
        probes.append(_probe_redis(settings.redis_url))
    else:
        probes.append(asyncio.sleep(0, result=_unknown("Redis Ephemeral Cache", "disabled_in_config")))

    results = await asyncio.gather(*probes)
    
    # Aggregation rules
    # OBSERVED_UNAVAILABLE -> red
    # OBSERVED_DEGRADED -> orange
    # UNKNOWN_NOT_OBSERVED -> unknown/gray
    # OBSERVED_HEALTHY -> green only when every required component is freshly observed healthy.
    
    has_unavailable = any(r["status"] == "OBSERVED_UNAVAILABLE" for r in results)
    has_degraded = any(r["status"] == "OBSERVED_DEGRADED" for r in results)
    has_unknown = any(r["status"] == "UNKNOWN_NOT_OBSERVED" for r in results)
    
    if has_unavailable:
        overall = "OBSERVED_UNAVAILABLE"
    elif has_degraded:
        overall = "OBSERVED_DEGRADED"
    elif has_unknown:
        overall = "UNKNOWN_NOT_OBSERVED"
    else:
        overall = "OBSERVED_HEALTHY"

    return {
        "timestamp": _timestamp(),
        "overall_status": overall,
        "observations": results
    }
