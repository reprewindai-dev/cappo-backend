"""Adversarial tests for P0-6 (Local Ollama topology & fail-closed resolution)."""

from __future__ import annotations

import pytest
import httpx
from cappo_backend.config import Settings
from cappo_backend.services.providers import (
    OllamaEndpointResolver,
    build_executor,
)
from cappo_backend.services.executor import LocalAuthorizerUnavailableError
from cappo_backend.security.ssrf import SSRFValidationError

class MockState:
    pass

class MockApp:
    def __init__(self):
        self.state = MockState()

def test_ollama_endpoint_resolver_checks() -> None:
    # 1. Disabled raises ValueError
    settings = Settings(_env_file=None, local_ollama_enabled=False)
    with pytest.raises(ValueError, match="not enabled"):
        OllamaEndpointResolver("http://127.0.0.1:11434", settings)

    # 2. Enabled but empty url raises ValueError
    settings = Settings(_env_file=None, local_ollama_enabled=True, ollama_upstream_url="")
    with pytest.raises(ValueError, match="empty"):
        OllamaEndpointResolver("", settings)

    # 3. External public IP raises validation error under LOCAL class rules
    settings = Settings(_env_file=None, local_ollama_enabled=True)
    with pytest.raises(SSRFValidationError):
        OllamaEndpointResolver("http://8.8.8.8:11434", settings)

    # 4. Valid private/loopback URL resolves successfully
    settings = Settings(_env_file=None, local_ollama_enabled=True)
    resolved = OllamaEndpointResolver("http://127.0.0.1:11434", settings)
    assert "127.0.0.1" in resolved

def test_local_ollama_fails_closed_without_authorizer() -> None:
    settings = Settings(
        _env_file=None,
        executor_mode="ollama",
        llm_provider_name="ollama",
        local_ollama_enabled=True,
        ollama_upstream_url="http://127.0.0.1:11434",
        allow_legacy_global_provider_config=True,
    )
    
    app = MockApp()
    ex = build_executor(settings, app=app)
    
    # Must fail closed with LocalAuthorizerUnavailableError
    with pytest.raises(LocalAuthorizerUnavailableError):
        ex.execute({
            "prompt": "hello",
            "pgl_id": "test",
            "authority_envelope": {"execution_id": "test", "allowed_provider_set": ["ollama"]}
        })

def test_tenant_byok_ollama_does_not_fail_closed() -> None:
    # Tenant-managed/BYOK Ollama does not require the local authorizer because it is tenant-controlled
    settings = Settings(
        _env_file=None,
        executor_mode="ollama",
        llm_provider_name="ollama",
        local_ollama_enabled=False, # local is disabled
        allow_legacy_global_provider_config=False,
    )
    
    # We construct a tenant Ollama executor directly to check it
    from cappo_backend.services.providers import OllamaExecutor
    # A tenant Ollama executor (is_local=False by default)
    exec_inst = OllamaExecutor(
        base_url="http://10.0.0.5:11434",
        model="llama3",
        is_local=False,
    )
    
    # Setup mock client transport to bypass real network call
    def handler(request):
        return httpx.Response(200, json={
            "model": "llama3",
            "message": {"content": "tenant response"},
            "done": True,
        })
    exec_inst._http()._transport = httpx.MockTransport(handler)
    
    # Executing this should succeed (no LocalAuthorizerUnavailableError raised!)
    res = exec_inst.execute({"prompt": "hi"})
    assert res["response"] == "tenant response"
