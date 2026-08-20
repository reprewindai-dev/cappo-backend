"""Provider clients + settings-driven executor factory tests."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cappo_backend.config import Settings
from cappo_backend.services.executor import (
    EchoExecutor,
    ExecutorUnavailableError,
    ResilientExecutor,
    TerminalExecutionError,
)
from cappo_backend.services.providers import (
    OllamaExecutor,
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


def _signed_503(private_key: Ed25519PrivateKey, body: bytes) -> dict[str, str]:
    created = int(datetime.now(UTC).timestamp())
    digest = f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode('ascii')}:"
    date = "Mon, 01 Jan 2026 12:00:00 GMT"
    params = f';created={created};keyid="provider-a"'
    base = "\n".join(
        [
            '"@status": 503',
            f'"content-digest": {digest}',
            f'"date": {date}',
            f'"@signature-params": ("@status" "content-digest" "date"){params}',
        ]
    ).encode()
    signature = base64.b64encode(private_key.sign(base)).decode()
    return {
        "content-digest": digest,
        "date": date,
        "signature-input": f'sig1=("@status" "content-digest" "date"){params}',
        "signature": f"sig1=:{signature}:",
    }


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
        name="openai",
        base_url="http://localhost:11434/v1",
        model="llama3",
        client=_client(handler),
    )
    assert ex.execute({"prompt": "x"})["provider"] == "openai"


def test_http_error_status_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    ex = OpenAICompatExecutor("openai", "https://api.test/v1", "m", client=_client(handler))
    with pytest.raises(ProviderError):
        ex.execute({"prompt": "x"})


def test_http_403_raises_terminal_authority_denial():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request, json={"detail": "denied"})

    ex = OpenAICompatExecutor("primary", "https://api.test/v1", "m", client=_client(handler))
    with pytest.raises(TerminalExecutionError, match="Authority Denied \\(403\\)"):
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


def test_build_executor_defaults_to_echo():
    assert isinstance(build_executor(Settings(_env_file=None)), EchoExecutor)


def test_build_executor_single_provider():
    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        allow_legacy_global_provider_config=True,
        llm_provider_name="groq",
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model="llama-3.1-8b-instant",
        llm_api_key="gsk-x",
    )
    ex = build_executor(settings)
    assert isinstance(ex, ResilientExecutor)
    assert [p.name for p in ex._providers] == ["groq"]


def test_build_executor_uses_native_ollama_and_base_url_env(monkeypatch):
    # Post-P0-0: The DAN self-proxy (OLLAMA_BASE_URL → DAN sidecar) has been
    # removed. OLLAMA_BASE_URL no longer influences executor routing; that
    # decoupling is intentional and documented here as a regression guard.
    # P0-6 will introduce OLLAMA_UPSTREAM_URL / OllamaEndpointResolver for
    # topology-aware local routing.
    # The executor uses llm_base_url directly, ignoring OLLAMA_BASE_URL.
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://10.42.0.12:11434")
    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        allow_legacy_global_provider_config=True,
        llm_provider_name="ollama",
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_model="qwen2.5:3b",
    )

    ex = build_executor(settings)
    provider_executor = ex._providers[0].executor

    assert isinstance(provider_executor, OllamaExecutor)
    # OllamaExecutor normalizes llm_base_url by stripping /v1.
    # The OLLAMA_BASE_URL env var ("http://10.42.0.12:11434") is NOT used.
    assert provider_executor._base_url == "http://127.0.0.1:11434"


def test_build_executor_with_ollama_fallback_uses_native_adapter():
    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        allow_legacy_global_provider_config=True,
        llm_provider_name="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="sk-x",
        llm_fallback_provider_name="ollama",
        llm_fallback_base_url="http://localhost:11434/v1",
        llm_fallback_model="llama3",
    )
    ex = build_executor(settings)
    assert [p.name for p in ex._providers] == ["openai", "ollama"]
    assert isinstance(ex._providers[1].executor, OllamaExecutor)
    assert ex._providers[1].executor._base_url == "http://localhost:11434"


def test_factory_executor_does_not_fail_over_on_unsigned_503():
    def fail(request):
        return httpx.Response(503, json={"error": "down"})

    def ok(request):
        return httpx.Response(200, json=_ok_response(content="from-fallback"))

    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        allow_legacy_global_provider_config=True,
        llm_provider_name="primary",
        llm_base_url="https://primary.test/v1",
        llm_fallback_provider_name="fallback",
        llm_fallback_base_url="https://fallback.test/v1",
    )
    ex = build_executor(settings)
    ex._providers[0].executor._client = _client(fail)
    ex._providers[1].executor._client = _client(ok)

    with pytest.raises(ExecutorUnavailableError, match="verified 503"):
        ex.execute({"prompt": "hi", "pgl_id": "test-user-id"})


def test_factory_executor_fails_over_after_signed_503_inside_authorized_set(monkeypatch):
    import cappo_backend.config as config

    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            vnp_federation_public_key=private_key.public_key().public_bytes_raw().hex(),
        ),
    )
    body = b'{"error":"unavailable"}'

    def fail(request):
        return httpx.Response(503, content=body, headers=_signed_503(private_key, body), request=request)

    def ok(request):
        return httpx.Response(200, json=_ok_response(content="from-fallback"), request=request)

    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        allow_legacy_global_provider_config=True,
        llm_provider_name="primary",
        llm_base_url="https://primary.test/v1",
        llm_fallback_provider_name="fallback",
        llm_fallback_base_url="https://fallback.test/v1",
    )
    ex = build_executor(settings)
    ex._providers[0].executor._client = _client(fail)
    ex._providers[1].executor._client = _client(ok)

    result = ex.execute(
        {
            "prompt": "hi",
            "authority_envelope": {"allowed_provider_set": ["primary", "fallback"]},
        }
    )

    assert result["response"] == "from-fallback"
    assert [attempt["provider_id"] for attempt in result["attempts"]] == ["primary", "fallback"]


def test_factory_executor_halts_when_all_real_clients_down():
    def fail(request):
        return httpx.Response(500, json={"error": "down"})

    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        allow_legacy_global_provider_config=True,
        llm_provider_name="primary",
        llm_base_url="https://primary.test/v1",
        llm_fallback_provider_name="fallback",
        llm_fallback_base_url="https://fallback.test/v1",
    )
    ex = build_executor(settings)
    ex._providers[0].executor._client = _client(fail)
    ex._providers[1].executor._client = _client(fail)
    with pytest.raises(ExecutorUnavailableError):
        ex.execute({"prompt": "x"})
