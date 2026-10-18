"""Tiered completion cache: key scoping, hot/warm tiers, and CachingExecutor.

A fake clock drives TTL deterministically; httpx.MockTransport stands in for
Upstash so the warm tier is testable offline.
"""

from __future__ import annotations

import json

import httpx
import pytest

from cappo_backend.config import Settings
from cappo_backend.services.cache import (
    CachingExecutor,
    HotCache,
    InMemoryWarmCache,
    RedisWarmCache,
    UpstashWarmCache,
    cache_key,
)
from cappo_backend.services.providers import build_executor


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class CountingExecutor:
    """Inner executor that counts calls and returns a unique payload."""

    provider = "counting"

    def __init__(self):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return {
            "response": f"resp-{self.calls}-{request.get('prompt')}",
            "model": "m",
            "provider": self.provider,
            "tokens": 1,
        }


# --------------------------------------------------------------------------
# cache_key
# --------------------------------------------------------------------------


def test_cache_key_is_deterministic_for_same_inputs():
    a = cache_key({"prompt": "hi", "pgl_id": "test-user-id", "model": "m", "workspace_id": "w1"})
    b = cache_key({"prompt": "hi", "pgl_id": "test-user-id", "model": "m", "workspace_id": "w1"})
    assert a == b


def test_cache_key_isolates_tenants_and_workspaces():
    base = {"prompt": "hi", "pgl_id": "test-user-id", "model": "m"}
    assert cache_key({**base, "workspace_id": "w1"}) != cache_key(
        {**base, "workspace_id": "w2"}
    )
    assert cache_key({**base, "tenant_id": "t1"}) != cache_key(
        {**base, "tenant_id": "t2"}
    )


def test_cache_key_changes_with_prompt_and_params():
    assert cache_key({"prompt": "a"}) != cache_key({"prompt": "b"})
    assert cache_key({"prompt": "a", "temperature": 0.1}) != cache_key(
        {"prompt": "a", "temperature": 0.9}
    )


# --------------------------------------------------------------------------
# HotCache
# --------------------------------------------------------------------------


def test_hot_cache_hit_and_ttl_expiry():
    clock = FakeClock()
    hot = HotCache(ttl=10, time_fn=clock)
    hot.set("k", {"v": 1})
    assert hot.get("k") == {"v": 1}
    clock.advance(10)
    assert hot.get("k") is None  # expired


def test_hot_cache_lru_eviction():
    hot = HotCache(max_size=2, ttl=1000)
    hot.set("a", {"v": "a"})
    hot.set("b", {"v": "b"})
    hot.get("a")  # touch a -> b is now LRU
    hot.set("c", {"v": "c"})  # evicts b
    assert hot.get("b") is None
    assert hot.get("a") == {"v": "a"}
    assert hot.get("c") == {"v": "c"}


# --------------------------------------------------------------------------
# InMemoryWarmCache
# --------------------------------------------------------------------------


def test_warm_memory_hit_and_expiry():
    clock = FakeClock()
    warm = InMemoryWarmCache(time_fn=clock)
    warm.set("k", {"v": 1}, ttl=5)
    assert warm.get("k") == {"v": 1}
    clock.advance(5)
    assert warm.get("k") is None


# --------------------------------------------------------------------------
# UpstashWarmCache (mocked REST transport)
# --------------------------------------------------------------------------


def test_upstash_set_then_get_roundtrip():
    store = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cmd = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer tok"
        if cmd[0] == "SET":
            store[cmd[1]] = cmd[2]
            return httpx.Response(200, json={"result": "OK"})
        if cmd[0] == "GET":
            return httpx.Response(200, json={"result": store.get(cmd[1])})
        return httpx.Response(400, json={"error": "bad"})

    client = httpx.Client(base_url="https://up.test", transport=httpx.MockTransport(handler))
    warm = UpstashWarmCache(url="https://up.test", token="tok", client=client)
    warm.set("k", {"v": 7}, ttl=60)
    assert warm.get("k") == {"v": 7}
    assert warm.get("missing") is None


def test_upstash_fails_soft_to_miss_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    client = httpx.Client(base_url="https://up.test", transport=httpx.MockTransport(handler))
    warm = UpstashWarmCache(url="https://up.test", token="tok", client=client)
    # Must not raise — a broken warm tier degrades to a miss.
    assert warm.get("k") is None
    warm.set("k", {"v": 1}, ttl=60)  # also must not raise


# --------------------------------------------------------------------------
# RedisWarmCache (injected fake client)
# --------------------------------------------------------------------------


class FakeRedis:
    """Minimal stand-in for redis.Redis (get/set with ex)."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value


def test_redis_set_then_get_roundtrip():
    warm = RedisWarmCache(url="redis://x", client=FakeRedis())
    warm.set("k", {"v": 9}, ttl=60)
    assert warm.get("k") == {"v": 9}
    assert warm.get("missing") is None


def test_redis_fails_soft_to_miss_on_error():
    class Boom:
        def get(self, key):
            raise RuntimeError("connection refused")

        def set(self, key, value, ex=None):
            raise RuntimeError("connection refused")

    warm = RedisWarmCache(url="redis://x", client=Boom())
    assert warm.get("k") is None  # must not raise
    warm.set("k", {"v": 1}, ttl=60)  # must not raise


# --------------------------------------------------------------------------
# CachingExecutor
# --------------------------------------------------------------------------


def test_miss_calls_inner_then_caches():
    inner = CountingExecutor()
    ex = CachingExecutor(inner, HotCache(), InMemoryWarmCache())
    out1 = ex.execute({"prompt": "hi", "pgl_id": "test-user-id", "workspace_id": "w"})
    assert out1["cached"] is False and out1["cache_tier"] is None
    assert inner.calls == 1


def test_second_identical_request_hits_hot_without_calling_inner():
    inner = CountingExecutor()
    ex = CachingExecutor(inner, HotCache(), InMemoryWarmCache())
    req = {"prompt": "hi", "pgl_id": "test-user-id", "workspace_id": "w"}
    first = ex.execute(req)
    second = ex.execute(req)
    assert inner.calls == 1  # inner not called again
    assert second["cached"] is True and second["cache_tier"] == "hot"
    assert second["response"] == first["response"]


def test_warm_hit_promotes_to_hot():
    inner = CountingExecutor()
    hot = HotCache()
    warm = InMemoryWarmCache()
    ex = CachingExecutor(inner, hot, warm)
    req = {"prompt": "hi", "pgl_id": "test-user-id", "workspace_id": "w"}
    ex.execute(req)  # populates warm + hot
    # Simulate a fresh worker: clear hot, keep warm.
    hot._store.clear()
    out = ex.execute(req)
    assert out["cached"] is True and out["cache_tier"] == "warm"
    assert inner.calls == 1  # served from warm, inner not re-called
    # And it was promoted back into hot.
    assert ex.execute(req)["cache_tier"] == "hot"


def test_different_tenants_do_not_share_cache():
    inner = CountingExecutor()
    ex = CachingExecutor(inner, HotCache(), InMemoryWarmCache())
    ex.execute({"prompt": "hi", "pgl_id": "test-user-id", "tenant_id": "t1"})
    ex.execute({"prompt": "hi", "pgl_id": "test-user-id", "tenant_id": "t2"})
    assert inner.calls == 2  # distinct keys -> both miss


def test_failed_inner_is_not_cached():
    class Boom:
        provider = "boom"

        def execute(self, request):
            raise RuntimeError("down")

    ex = CachingExecutor(Boom(), HotCache(), InMemoryWarmCache())
    with pytest.raises(RuntimeError):
        ex.execute({"prompt": "x"})


# --------------------------------------------------------------------------
# build_executor wiring
# --------------------------------------------------------------------------


def test_build_executor_wraps_with_cache_when_enabled():
    settings = Settings(executor_mode="echo", cache_enabled=True)
    ex = build_executor(settings)
    assert isinstance(ex, CachingExecutor)
    out = ex.execute({"prompt": "hello", "pgl_id": "test-user-id", "workspace_id": "w"})
    assert out["cached"] is False
    assert ex.execute({"prompt": "hello", "pgl_id": "test-user-id", "workspace_id": "w"})["cached"] is True


def test_build_executor_no_cache_by_default():
    ex = build_executor(Settings(executor_mode="echo"))
    assert not isinstance(ex, CachingExecutor)


def test_upstash_backend_requires_credentials():
    settings = Settings(
        executor_mode="echo", cache_enabled=True, cache_warm_backend="upstash"
    )
    with pytest.raises(ValueError):
        build_executor(settings)


def test_redis_backend_requires_url():
    settings = Settings(
        executor_mode="echo", cache_enabled=True, cache_warm_backend="redis"
    )
    with pytest.raises(ValueError):
        build_executor(settings)
