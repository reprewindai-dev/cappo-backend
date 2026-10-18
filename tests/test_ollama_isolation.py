from __future__ import annotations

import json

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from cappo_backend.config import Settings
from cappo_backend.core.security.ollama_sanitizer import OllamaBleedSanitizerMiddleware
from cappo_backend.services.providers import OllamaExecutor, build_executor


def test_native_ollama_request_transmits_keep_alive_zero() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {"role": "assistant", "content": "ok"},
                "prompt_eval_count": 3,
                "eval_count": 2,
            },
        )

    client = httpx.Client(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    executor = OllamaExecutor(
        base_url="http://ollama.test/v1",
        model="qwen2.5:3b",
        client=client,
    )

    result = executor.execute({"prompt": "hello", "temperature": 0.2, "max_tokens": 10})

    assert captured["url"] == "http://ollama.test/api/chat"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["keep_alive"] == 0
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.2, "num_predict": 10}
    assert result["response"] == "ok"
    assert result["provider"] == "ollama"
    assert result["tokens"] == 5


def test_ollama_factory_uses_native_executor(monkeypatch) -> None:
    # Post-P0-0: OLLAMA_BASE_URL no longer redirects through the DAN proxy.
    # The DAN self-proxy routing was a prototype that has been removed.
    # The executor now uses llm_base_url directly; OLLAMA_BASE_URL has no
    # routing effect (P0-6 will introduce OLLAMA_UPSTREAM_URL for that).
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.internal:11434")
    settings = Settings(
        _env_file=None,
        executor_mode="openai",
        allow_legacy_global_provider_config=True,
        llm_provider_name="ollama",
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_model="qwen2.5:3b",
    )

    executor = build_executor(settings)
    provider_executor = executor._providers[0].executor

    assert isinstance(provider_executor, OllamaExecutor)
    # OllamaExecutor strips the /v1 suffix from llm_base_url.
    # OLLAMA_BASE_URL ("http://ollama.internal:11434") is ignored for routing.
    assert provider_executor._base_url == "http://127.0.0.1:11434"


def test_middleware_signal_is_not_promoted_to_sanitized_proof() -> None:
    app = FastAPI()
    app.add_middleware(OllamaBleedSanitizerMiddleware)

    @app.post("/api/v1/inference")
    async def inference(request: Request) -> dict[str, object]:
        return {
            "policy_signal": request.headers.get(
                "x-veklom-require-ollama-keep-alive-zero"
            )
        }

    response = TestClient(app).post("/api/v1/inference", json={"prompt": "hello"})

    assert response.status_code == 200
    assert response.json()["policy_signal"] == "true"
    assert "X-Ollama-Context-Sanitized" not in response.headers
