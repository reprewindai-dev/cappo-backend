"""OpenAI-compatible provider client + settings-driven executor factory.

Uses httpx.MockTransport so no network or API key is needed: we assert the
outgoing request shape, response parsing, and that network/HTTP/JSON failures
raise ProviderError (which the circuit breaker records and fails over on).
"""

from __future__ import annotations

import httpx
import pytest

from cappo_backend.config import Settings
from cappo_backend.services.executor import (
    EchoExecutor,
    ExecutorUnavailableError,
    ResilientExecutor,
)
from cappo_backend.services.providers import (
    OpenAICompatExecutor,
    ProviderError,
    build_executor,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(base_url="https://api.test/v1", transport=httpx.MockTransport(handler))


def _ok_response(content="hello", total_tokens=7, model="gpt-4o-mini"):
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"total_tokens": total_tokens},
    }


# --------------------------------------------------------------------------
# Request shaping + response parsing
# --------------------------------------------------------------------------


def test_sends_openai_chat_request_and_parses_response():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_response(content="hi there", total_tokens=12))

    ex = OpenAICompatExecutor(
        name="openai",
        base_url="https://api.test/v1",
        model="gpt-4o-mini",
        api_key="sk-test",
        client=_client(handler),
    )
    out = ex.execute({"prompt": "say hi", "temperature": 0.2})

    assert captured["url"] == "https://api.test/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["messages"] == [{"role": "user", "content": "say hi"}]
    assert captured["body"]["temperature"] == 0.2
    assert out == {
        "response": "hi there",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "tokens": 12,
    }


def test_omits_authorization_header_when_no_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=_ok_response())

    ex = OpenAICompatExecutor(
        name="ollama", base_url="http://localhost:11434/v1", model="llama3", client=_client(handler)
    )
    assert ex.execute({"prompt": "x"})["provider"] == "ollama"


# --------------------------------------------------------------------------
# Failure modes -> ProviderError (so the breaker counts them)
# --------------------------------------------------------------------------


def test_http_error_status_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    ex = OpenAICompatExecutor("openai", "https://api.test/v1", "m", client=_client(handler))
    with pytest.raises(ProviderError):
        ex.execute({"prompt": "x"})


def test_network_error_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    ex = OpenAICompatExecutor("openai", "https://api.test/v1", "m", client=_client(handler))
    with pytest.raises(ProviderError):
        ex.execute({"prompt": "x"})


def test_malformed_response_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    ex = OpenAICompatExecutor("openai", "https://api.test/v1", "m", client=_client(handler))
    with pytest.raises(ProviderError):
        ex.execute({"prompt": "x"})


# --------------------------------------------------------------------------
# Settings-driven factory
# --------------------------------------------------------------------------


def test_build_executor_defaults_to_echo():
    assert isinstance(build_executor(Settings()), EchoExecutor)


def test_build_executor_single_provider():
    settings = Settings(
        executor_mode="openai",
        llm_provider_name="groq",
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model="llama-3.1-8b-instant",
        llm_api_key="gsk-x",
    )
    ex = build_executor(settings)
    assert isinstance(ex, ResilientExecutor)
    assert [p.name for p in ex._providers] == ["groq"]


def test_build_executor_with_fallback():
    settings = Settings(
        executor_mode="openai",
        llm_provider_name="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="sk-x",
        llm_fallback_provider_name="ollama",
        llm_fallback_base_url="http://localhost:11434/v1",
        llm_fallback_model="llama3",
    )
    ex = build_executor(settings)
    assert [p.name for p in ex._providers] == ["openai", "ollama"]


def test_factory_executor_fails_over_across_real_clients():
    """End-to-end: primary 500s, breaker records it, fallback (mock) serves."""

    def fail(request):
        return httpx.Response(503, json={"error": "down"})

    def ok(request):
        return httpx.Response(200, json=_ok_response(content="from-fallback"))

    settings = Settings(
        executor_mode="openai",
        llm_provider_name="primary",
        llm_base_url="https://primary.test/v1",
        llm_fallback_provider_name="fallback",
        llm_fallback_base_url="https://fallback.test/v1",
    )
    ex = build_executor(settings)
    # Swap in mock-backed clients on the constructed providers.
    ex._providers[0].executor._client = _client(fail)
    ex._providers[1].executor._client = _client(ok)

    out = ex.execute({"prompt": "hi"})
    assert out["response"] == "from-fallback"
    assert out["provider"] == "fallback"


def test_factory_executor_halts_when_all_real_clients_down():
    def fail(request):
        return httpx.Response(500, json={"error": "down"})

    settings = Settings(
        executor_mode="openai",
        llm_base_url="https://primary.test/v1",
        llm_fallback_base_url="https://fallback.test/v1",
    )
    ex = build_executor(settings)
    ex._providers[0].executor._client = _client(fail)
    ex._providers[1].executor._client = _client(fail)
    with pytest.raises(ExecutorUnavailableError):
        ex.execute({"prompt": "x"})
