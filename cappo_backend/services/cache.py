"""Tiered completion cache for execution-layer latency.

Two tiers sit in front of the LLM providers:

* **hot**  — in-process exact-match cache (TTL + LRU). No network; serves repeat
  prompts on the same worker in microseconds.
* **warm** — shared, out-of-process store (Upstash Redis REST) that survives
  restarts/deploys and is shared across workers. A warm hit re-populates hot.

Governance note: this cache lives strictly *inside the execution layer*. The
governed pipeline (PGL pre-cert → EI mint → LAW 0 gateway → attest) still runs on
every request; a cache hit only short-circuits the **provider call**, never the
governance. Cache keys are scoped by workspace/tenant so cached outputs are never
shared across isolation boundaries.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

import httpx

from cappo_backend.services.canonical import sha256_json

logger = logging.getLogger("cappo.cache")


def cache_key(request: dict[str, Any], namespace: str = "cappo") -> str:
    """Deterministic, tenant-scoped cache key for a completion request.

    Only inputs that affect the model output (and the isolation boundary) are
    included, so semantically identical requests collide and different tenants
    never do.
    """
    workspace_id = request.get("workspace_id", "default")
    keyed = {
        "model": request.get("model"),
        "prompt": request.get("prompt", ""),
        "temperature": request.get("temperature"),
        "max_tokens": request.get("max_tokens"),
        "workspace_id": workspace_id,
        "tenant_id": request.get("tenant_id"),
        "scope": request.get("scope"),
        "constitution_hash": request.get("constitution_hash"),
        "genome_hash": request.get("genome_hash"),
        "plan_hash": request.get("plan_hash"),
        "action": request.get("action"),
        "execution_mode": request.get("execution_mode"),
    }
    return f"{namespace}:{workspace_id}:completion:{sha256_json(keyed)}"


@runtime_checkable
class WarmCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any], ttl: int) -> None: ...


class InMemoryWarmCache:
    """Process-local warm tier for dev/test (and a safe default).

    Mimics a shared store's get/set+TTL contract without external infra so the
    caching path is fully testable offline. ``time_fn`` is injectable.
    """

    def __init__(self, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._data: dict[str, tuple[float, dict[str, Any]]] = {}
        self._time_fn = time_fn

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._time_fn() >= expires_at:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        self._data[key] = (self._time_fn() + ttl, value)


class UpstashWarmCache:
    """Warm tier backed by Upstash Redis over its REST API.

    Commands are issued as JSON arrays (``["SET", key, value, "EX", ttl]``) to
    the REST base URL with a Bearer token. Values are stored as canonical JSON.
    Any transport/HTTP error is swallowed to a cache miss — the cache must never
    break execution; on miss we simply call the provider.
    """

    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = 2.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client = client

    def get(self, key: str) -> dict[str, Any] | None:
        result = self._command(["GET", key])
        if not result:
            return None
        import json

        try:
            return json.loads(result)
        except (ValueError, TypeError):
            return None

    def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        import json

        self._command(["SET", key, json.dumps(value), "EX", str(ttl)])

    def _command(self, command: list[str]) -> Any:
        try:
            resp = self._http().post(
                "/",
                json=command,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            resp.raise_for_status()
            return resp.json().get("result")
        except (httpx.HTTPError, ValueError) as exc:
            # Fail-soft: a broken warm tier degrades to a miss, never an error.
            logger.warning("warm cache unavailable", extra={"error": str(exc)})
            return None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self._url, timeout=self._timeout)
        return self._client


class RedisWarmCache:
    """Warm tier backed by a Redis server over TCP (redis-py).

    Reads a standard connection URL (``redis://`` or ``rediss://`` for TLS), so
    it works against a self-hosted Redis *and* managed Redis (e.g. Upstash's
    ``rediss://`` endpoint). Values are canonical JSON. Any Redis error degrades
    to a miss — the cache must never break execution.
    """

    def __init__(self, url: str, timeout: float = 2.0, client: Any | None = None) -> None:
        self._url = url
        self._timeout = timeout
        self._client = client

    def get(self, key: str) -> dict[str, Any] | None:
        import json

        try:
            raw = self._redis().get(key)
        except Exception as exc:  # redis.exceptions.RedisError and connection issues
            logger.warning("warm cache (redis) unavailable", extra={"error": str(exc)})
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        import json

        try:
            self._redis().set(key, json.dumps(value), ex=ttl)
        except Exception as exc:
            logger.warning("warm cache (redis) set failed", extra={"error": str(exc)})

    def _redis(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                self._url,
                socket_timeout=self._timeout,
                socket_connect_timeout=self._timeout,
                decode_responses=True,
            )
        return self._client


class HotCache:
    """In-process exact-match cache with TTL + LRU eviction."""

    def __init__(
        self,
        max_size: int = 1024,
        ttl: int = 300,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._time_fn = time_fn

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._time_fn() >= expires_at:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)  # LRU: mark recently used
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._store[key] = (self._time_fn() + self._ttl, value)
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)  # evict least-recently-used


class CachingExecutor:
    """Wraps an inner executor with a tiered (hot → warm → provider) cache.

    Satisfies the :class:`~cappo_backend.services.executor.Executor` protocol, so
    it composes with ``ResilientExecutor``: lookups try hot, then warm (writing
    back to hot on a warm hit); a miss calls the inner executor and writes the
    result to both tiers. The returned dict carries ``cached`` (bool) and
    ``cache_tier`` ("hot"/"warm"/None) for audit/observability.
    """

    def __init__(
        self,
        inner: Any,
        hot: HotCache,
        warm: WarmCache,
        ttl: int = 300,
        namespace: str = "cappo",
    ) -> None:
        self._inner = inner
        self._hot = hot
        self._warm = warm
        self._ttl = ttl
        self._namespace = namespace

    @property
    def provider(self) -> str:
        return getattr(self._inner, "provider", "cached")

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        cache_allowed = request.get("cache_allowed", True)
        if not cache_allowed:
            result = self._inner.execute(request)
            return {**result, "cached": False, "cache_tier": None}

        key = cache_key(request, self._namespace)

        hit = self._hot.get(key)
        if hit is not None:
            logger.info("cache hit", extra={"tier": "hot"})
            return {**hit, "cached": True, "cache_tier": "hot"}

        hit = self._warm.get(key)
        if hit is not None:
            logger.info("cache hit", extra={"tier": "warm"})
            self._hot.set(key, hit)  # promote into hot
            return {**hit, "cached": True, "cache_tier": "warm"}

        result = self._inner.execute(request)
        # Only successful provider results are stored (the inner raises on
        # failure, which propagates to the circuit breaker — never cached).
        self._hot.set(key, result)
        self._warm.set(key, result, self._ttl)
        return {**result, "cached": False, "cache_tier": None}
