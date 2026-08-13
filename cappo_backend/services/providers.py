"""Real execution-layer providers.

Provider clients stay behind the governed execution boundary. OpenAI-compatible
providers use ``POST /chat/completions``. Ollama is intentionally different:
its model lifecycle controls are exposed by the native ``POST /api/chat`` API,
so CAPPO uses a dedicated native adapter whenever the configured provider name
is ``ollama``.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

from cappo_backend.services.cache import (
    CachingExecutor,
    HotCache,
    InMemoryWarmCache,
    RedisWarmCache,
    UpstashWarmCache,
    WarmCache,
)
from cappo_backend.services.circuit_breaker import CircuitBreaker
from cappo_backend.services.executor import (
    EchoExecutor,
    Executor,
    Provider,
    ResilientExecutor,
    TerminalExecutionError,
)

if TYPE_CHECKING:
    from cappo_backend.config import Settings

logger = logging.getLogger("cappo.provider")

DEFAULT_TIMEOUT = 30.0


class ProviderError(RuntimeError):
    """Raised when a provider call fails."""


class OpenAICompatExecutor:
    """OpenAI-compatible chat-completions executor."""

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.provider = name
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = client

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        prompt = request.get("prompt", "")
        payload: dict[str, Any] = {
            "model": request.get("model") or self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if "temperature" in request:
            payload["temperature"] = request["temperature"]
        if "max_tokens" in request:
            payload["max_tokens"] = request["max_tokens"]

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = self._http().post("/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise TerminalExecutionError(
                    f"Authority Denied (403): {self.provider} returned HTTP 403"
                ) from exc
            raise ProviderError(
                f"{self.provider} returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.provider} request failed: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"{self.provider} returned invalid JSON") from exc

        return self._parse(data, payload["model"])

    def _parse(self, data: dict[str, Any], model: str) -> dict[str, Any]:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"{self.provider} response missing choices[0].message.content"
            ) from exc
        usage = data.get("usage") or {}
        return {
            "response": content,
            "model": data.get("model", model),
            "provider": self.provider,
            "tokens": int(usage.get("total_tokens", 0)),
        }

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        return self._client


class OllamaExecutor:
    """Native Ollama chat executor with request-bound model unloading.

    ``keep_alive=0`` is sent on every native ``/api/chat`` request. This is the
    enforcement boundary; middleware headers are never treated as proof that the
    provider honored a lifecycle control.
    """

    provider = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
        name: str = "ollama",
    ) -> None:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v1"):
            normalized = normalized[:-3]
        self.provider = name
        self.model = model
        self._base_url = normalized.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = client

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        prompt = request.get("prompt", "")
        payload: dict[str, Any] = {
            "model": request.get("model") or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": 0,
        }
        options: dict[str, Any] = {}
        if "temperature" in request:
            options["temperature"] = request["temperature"]
        if "max_tokens" in request:
            options["num_predict"] = request["max_tokens"]
        if options:
            payload["options"] = options

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = self._http().post("/api/chat", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"{self.provider} returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.provider} request failed: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"{self.provider} returned invalid JSON") from exc

        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError(
                f"{self.provider} response missing message.content"
            ) from exc
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        return {
            "response": content,
            "model": data.get("model", payload["model"]),
            "provider": self.provider,
            "tokens": prompt_tokens + completion_tokens,
        }

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        return self._client


def _breaker(settings: Settings, name: str) -> CircuitBreaker:
    return CircuitBreaker(
        name=name,
        failure_threshold=settings.breaker_failure_threshold,
        recovery_timeout=settings.breaker_recovery_timeout,
        success_threshold=settings.breaker_success_threshold,
    )


def _build_warm_cache(settings: Settings) -> WarmCache:
    backend = settings.cache_warm_backend.lower()
    if backend == "redis":
        if not settings.redis_url:
            raise ValueError("cache_warm_backend='redis' requires REDIS_URL")
        return RedisWarmCache(url=settings.redis_url)
    if backend == "upstash":
        if not (settings.upstash_redis_rest_url and settings.upstash_redis_rest_token):
            raise ValueError(
                "cache_warm_backend='upstash' requires UPSTASH_REDIS_REST_URL "
                "and UPSTASH_REDIS_REST_TOKEN"
            )
        return UpstashWarmCache(
            url=settings.upstash_redis_rest_url,
            token=settings.upstash_redis_rest_token,
        )
    return InMemoryWarmCache()


def _maybe_wrap_cache(executor: Executor, settings: Settings) -> Executor:
    if not settings.cache_enabled:
        return executor
    hot = HotCache(max_size=settings.hot_cache_max_size, ttl=settings.cache_ttl_seconds)
    warm = _build_warm_cache(settings)
    return CachingExecutor(
        inner=executor,
        hot=hot,
        warm=warm,
        ttl=settings.cache_ttl_seconds,
        namespace=settings.cache_namespace,
    )


_breaker_registry: dict[str, CircuitBreaker] = {}


def _provider_base_url(settings: Settings) -> str:
    """Resolve the configured provider base URL without committing topology."""
    base_url = settings.llm_base_url
    if settings.llm_provider_name.lower() == "ollama":
        ollama_base_url = os.getenv("OLLAMA_BASE_URL")
        loopback_defaults = {
            "http://127.0.0.1:11434/v1",
            "http://localhost:11434/v1",
            "http://127.0.0.1:11434",
            "http://localhost:11434",
        }
        if ollama_base_url and settings.llm_base_url.rstrip("/") in loopback_defaults:
            base_url = ollama_base_url
    return base_url.rstrip("/")


def _provider_executor(
    *,
    name: str,
    base_url: str,
    model: str,
    api_key: str | None,
    timeout: float,
) -> Executor:
    if name.lower() == "ollama":
        return OllamaExecutor(
            name=name,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
        )
    return OpenAICompatExecutor(
        name=name,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
    )


def build_executor(settings: Settings) -> Executor:
    """Construct the execution-layer executor from settings."""
    if settings.executor_mode.lower() == "echo":
        return _maybe_wrap_cache(EchoExecutor(), settings)

    primary_name = settings.llm_provider_name
    primary_breaker = _breaker(settings, primary_name)
    _breaker_registry[primary_name] = primary_breaker
    providers: list[Provider] = [
        Provider(
            name=primary_name,
            executor=_provider_executor(
                name=primary_name,
                base_url=_provider_base_url(settings),
                model=settings.llm_model,
                api_key=settings.llm_api_key or None,
                timeout=settings.llm_timeout_seconds,
            ),
            breaker=primary_breaker,
        )
    ]

    if settings.llm_fallback_base_url:
        fallback_name = settings.llm_fallback_provider_name or "fallback"
        fallback_breaker = _breaker(settings, fallback_name)
        _breaker_registry[fallback_name] = fallback_breaker
        providers.append(
            Provider(
                name=fallback_name,
                executor=_provider_executor(
                    name=fallback_name,
                    base_url=settings.llm_fallback_base_url,
                    model=settings.llm_fallback_model or settings.llm_model,
                    api_key=settings.llm_fallback_api_key or None,
                    timeout=settings.llm_timeout_seconds,
                ),
                breaker=fallback_breaker,
            )
        )
    return _maybe_wrap_cache(ResilientExecutor(providers), settings)
