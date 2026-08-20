"""Adversarial tests for P0-5 (Tenant-safe cache & correct resource lifecycle)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock
import httpx

from cappo_backend.config import Settings
from cappo_backend.services.providers import (
    build_executor,
    _shared_http_clients,
    _test_hot_cache,
    _test_warm_cache,
)
from cappo_backend.services.cache import CachingExecutor, HotCache, cache_key

class MockState:
    pass

class MockApp:
    def __init__(self):
        self.state = MockState()

def test_caching_and_workspace_isolation() -> None:
    settings = Settings(
        _env_file=None,
        executor_mode="echo",
        cache_enabled=True,
        cache_ttl_seconds=300,
    )
    app = MockApp()
    
    # 1. Build executor for Workspace A
    exec_a = build_executor(settings, workspace_id="workspace_a", app=app)
    
    req_a = {
        "model": "gpt-4",
        "prompt": "hello world",
        "workspace_id": "workspace_a",
    }
    
    # First call - cache miss
    res1 = exec_a.execute(req_a)
    assert res1["cached"] is False
    assert res1["cache_tier"] is None
    
    # Second call for A - cache hit (hot cache)
    res2 = exec_a.execute(req_a)
    assert res2["cached"] is True
    assert res2["cache_tier"] == "hot"
    
    # 2. Build executor for Workspace B and execute same prompt
    exec_b = build_executor(settings, workspace_id="workspace_b", app=app)
    
    req_b = {
        "model": "gpt-4",
        "prompt": "hello world",
        "workspace_id": "workspace_b",
    }
    
    # Must be cache miss because of workspace isolation!
    res_b = exec_b.execute(req_b)
    assert res_b["cached"] is False
    assert res_b["cache_tier"] is None

def test_cache_allowed_policy_gate() -> None:
    settings = Settings(
        _env_file=None,
        executor_mode="echo",
        cache_enabled=True,
    )
    app = MockApp()
    
    exec_inst = build_executor(settings, workspace_id="workspace_c", app=app)
    
    req_no_cache = {
        "model": "gpt-4",
        "prompt": "do not cache me",
        "workspace_id": "workspace_c",
        "cache_allowed": False,
    }
    
    # First call with cache_allowed=False - cache miss
    res1 = exec_inst.execute(req_no_cache)
    assert res1["cached"] is False
    
    # Second call with cache_allowed=False - still cache miss
    res2 = exec_inst.execute(req_no_cache)
    assert res2["cached"] is False
    
    # Call with cache_allowed=True (same prompt) - still cache miss because it wasn't written!
    req_cache = {
        "model": "gpt-4",
        "prompt": "do not cache me",
        "workspace_id": "workspace_c",
        "cache_allowed": True,
    }
    res3 = exec_inst.execute(req_cache)
    assert res3["cached"] is False

def test_hot_cache_shared_across_requests() -> None:
    settings = Settings(
        _env_file=None,
        executor_mode="echo",
        cache_enabled=True,
    )
    app = MockApp()
    
    # Executor 1 constructed (simulating request 1)
    exec1 = build_executor(settings, workspace_id="workspace_d", app=app)
    req = {
        "model": "gpt-4",
        "prompt": "shared across request",
        "workspace_id": "workspace_d",
    }
    
    res1 = exec1.execute(req)
    assert res1["cached"] is False
    
    # Executor 2 constructed (simulating request 2 on a new executor instance)
    exec2 = build_executor(settings, workspace_id="workspace_d", app=app)
    res2 = exec2.execute(req)
    
    # Must be cache hit because they share the app-scoped HotCache!
    assert res2["cached"] is True
    assert res2["cache_tier"] == "hot"

def test_decrypted_credential_not_present_on_shared_objects() -> None:
    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        llm_provider_name="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="sk-test-key",
        allow_legacy_global_provider_config=True,
    )
    app = MockApp()
    
    # Build executor
    ex = build_executor(settings, app=app)
    
    # Perform a mocked HTTP request to trigger client creation
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "mocked"}}]
        })
    
    # Replace client transport with mock
    client = ex._providers[0].executor._http()
    client._transport = httpx.MockTransport(handler)
    
    # Execute request
    ex.execute({
        "prompt": "hello",
        "pgl_id": "test-id",
        "authority_envelope": {"execution_id": "test", "allowed_provider_set": ["openai"]}
    })
    
    # Verify that the shared http client instance has been registered on app state
    assert len(app.state.http_clients) > 0
    shared_client = list(app.state.http_clients.values())[0]
    
    # Assert that the decrypted LLM_API_KEY ("sk-test-key") is NOT stored on the shared client or app state
    assert not hasattr(shared_client, "api_key")
    assert not hasattr(shared_client, "_api_key")
    assert not hasattr(app.state, "api_key")
    
    # Verify that credentials are passed per-request during execute and not cached on long-lived executors
    assert not hasattr(ex._providers[0].executor, "_decrypted_key_material")
