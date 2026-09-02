import asyncio
import time
from typing import Any, Dict, List
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

def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _build_observation(
    service: str,
    endpoint: str,
    status: str,
    latency_ms: float = 0.0,
    http_class: str = "N/A",
    evidence_source: str = "cappo-backend-internal-probe"
) -> Dict[str, Any]:
    return {
        "service": service,
        "endpoint_used": endpoint,
        "observation_timestamp": _timestamp(),
        "http_class": http_class,
        "latency_ms": latency_ms,
        "evidence_source": evidence_source,
        "freshness_expiry_seconds": 60,
        "status": status
    }

def _unknown(service: str, endpoint: str = "unconfigured") -> Dict[str, Any]:
    return _build_observation(service, endpoint, "UNKNOWN_NOT_OBSERVED")

async def _probe_http(service: str, url: str) -> Dict[str, Any]:
    if not url:
        return _unknown(service)
    
    started = time.perf_counter()
    status = "UNKNOWN_NOT_OBSERVED"
    http_class = "TIMEOUT"
    
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            # We assume /health exists on the target
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            
            # Special case for ollama/executor which might be /api/tags or /
            path = "/health"
            if service == "Ollama Inference Nodes":
                path = "/"
                
            resp = await client.get(f"{base_url}{path}")
            latency = round((time.perf_counter() - started) * 1000, 2)
            http_class = f"{resp.status_code}"
            
            if 200 <= resp.status_code < 300:
                status = "OBSERVED_HEALTHY"
            elif resp.status_code >= 500:
                status = "OBSERVED_UNAVAILABLE"
            else:
                status = "OBSERVED_DEGRADED"
                
            return _build_observation(service, f"{base_url}{path}", status, latency, http_class)
    except asyncio.TimeoutError:
        return _build_observation(service, url, "UNKNOWN_NOT_OBSERVED", 0, "TIMEOUT")
    except Exception as e:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation(service, url, "OBSERVED_UNAVAILABLE", latency, "CONNECTION_ERROR")

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
        return _build_observation("Redis Ephemeral Cache", urlparse(url).hostname or "redis", status, latency, "PING_OK" if available else "PING_FAIL")
    except asyncio.TimeoutError:
        return _build_observation("Redis Ephemeral Cache", urlparse(url).hostname or "redis", "UNKNOWN_NOT_OBSERVED", 0, "TIMEOUT")
    except Exception:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation("Redis Ephemeral Cache", urlparse(url).hostname or "redis", "OBSERVED_UNAVAILABLE", latency, "CONNECTION_ERROR")

async def _probe_database() -> Dict[str, Any]:
    started = time.perf_counter()
    def check() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    if _DATABASE_PROBE_LOCK.locked():
        return _build_observation("PostgreSQL Shared State", "sqlalchemy_engine", "UNKNOWN_NOT_OBSERVED", 0, "LOCK_CONTENTION")

    try:
        async with _DATABASE_PROBE_LOCK:
            await asyncio.wait_for(asyncio.to_thread(check), timeout=_PROBE_TIMEOUT_SECONDS)
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation("PostgreSQL Shared State", "sqlalchemy_engine", "OBSERVED_HEALTHY", latency, "SQL_OK")
    except asyncio.TimeoutError:
        return _build_observation("PostgreSQL Shared State", "sqlalchemy_engine", "UNKNOWN_NOT_OBSERVED", 0, "TIMEOUT")
    except Exception:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return _build_observation("PostgreSQL Shared State", "sqlalchemy_engine", "OBSERVED_UNAVAILABLE", latency, "CONNECTION_ERROR")

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
        probes.append(asyncio.sleep(0, result=_unknown("Ollama Inference Nodes")))
        
    if settings.cache_warm_backend.lower() == "redis":
        probes.append(_probe_redis(settings.redis_url))
    else:
        probes.append(asyncio.sleep(0, result=_unknown("Redis Ephemeral Cache")))

    # Hard-to-probe services without dedicated URLs in settings
    probes.append(asyncio.sleep(0, result=_unknown("LockerPhycer (Vault)")))
    probes.append(asyncio.sleep(0, result=_unknown("VNP Micro-Stakes Telemetry")))
    probes.append(asyncio.sleep(0, result=_unknown("RepoGate Security Scanner")))
    probes.append(asyncio.sleep(0, result=_unknown("x402 Micropayment Engine")))
    probes.append(asyncio.sleep(0, result=_unknown("UACP Control Plane")))

    results = await asyncio.gather(*probes)
    
    # Aggregation rules
    # If any is OBSERVED_UNAVAILABLE -> DEGRADED or DOWN overall
    # If all OBSERVED_HEALTHY or UNKNOWN_NOT_OBSERVED -> OPERATIONAL
    has_unavailable = any(r["status"] == "OBSERVED_UNAVAILABLE" for r in results)
    has_degraded = any(r["status"] == "OBSERVED_DEGRADED" for r in results)
    
    if has_unavailable:
        overall = "OBSERVED_UNAVAILABLE"
    elif has_degraded:
        overall = "OBSERVED_DEGRADED"
    else:
        overall = "OBSERVED_HEALTHY"

    return {
        "timestamp": _timestamp(),
        "overall_status": overall,
        "observations": results
    }
