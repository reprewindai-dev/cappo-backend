"""Real execution-layer providers.

The migration note (§2/§5) keeps provider clients behind a small interface,
invoked by the governed path. This implements a provider-agnostic
**OpenAI-compatible** HTTP client: OpenAI, Groq, and local Ollama all expose an
OpenAI-style ``POST {base_url}/chat/completions``, so a single client serves all
three by varying ``base_url`` + ``api_key``.

The client satisfies the :class:`~cappo_backend.services.executor.Executor`
protocol (``execute(request) -> dict``) so it drops straight into a
``Provider(...)`` slot behind a circuit breaker. Network/HTTP errors are raised
(never swallowed) so the breaker records them and fails over.
"""

from __future__ import annotations

import logging
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
)

if TYPE_CHECKING:
    from cappo_backend.config import Settings

logger = logging.getLogger("cappo.provider")

DEFAULT_TIMEOUT = 30.0


class ProviderError(RuntimeError):
    """Raised when a provider call fails (network, timeout, or HTTP status).

    Surfacing a single error type lets the circuit breaker treat any provider
    failure uniformly while preserving the original cause via ``__cause__``.
    """


class OpenAICompatExecutor:
    """OpenAI-compatible chat-completions executor.

    Parameters
    ----------
    name:
        Provider identifier (e.g. ``"openai"``, ``"groq"``, ``"ollama"``).
    base_url:
        API root, e.g. ``https://api.openai.com/v1``.
    model:
        Model id sent with each request.
    api_key:
        Bearer token. Optional for keyless local providers (Ollama).
    timeout:
        Per-request timeout in seconds.
    client:
        Optional pre-built :class:`httpx.Client` (inject a ``MockTransport`` in
        tests). When omitted a client is created lazily and reused.
    """

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

    # The orchestrator passes the whole run request_payload; we read prompt and
    # optional generation params from it and ignore the rest.
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
            response = self._http().post(
                "/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"{self.provider} returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:  # timeouts, connection errors, etc.
            raise ProviderError(f"{self.provider} request failed: {exc}") from exc
        except ValueError as exc:  # malformed JSON
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
    """Front the executor with the tiered completion cache when enabled."""
    if not settings.cache_enabled:
        return executor
    hot = HotCache(
        max_size=settings.hot_cache_max_size, ttl=settings.cache_ttl_seconds
    )
    warm = _build_warm_cache(settings)
    return CachingExecutor(
        inner=executor,
        hot=hot,
        warm=warm,
        ttl=settings.cache_ttl_seconds,
        namespace=settings.cache_namespace,
    )


# Module-level registry so platform_router can read live breaker states.
_breaker_registry: dict[str, CircuitBreaker] = {}


def build_executor(settings: Settings) -> Executor:
    """Construct the execution-layer executor from configuration.

    ``executor_mode="echo"`` (default) returns the deterministic stub. Otherwise
    the primary OpenAI-compatible provider is wired behind its own breaker, with
    an optional fallback provider (enabled when ``llm_fallback_base_url`` is set),
    yielding a :class:`ResilientExecutor`. When ``cache_enabled`` is set the
    result is fronted by the tiered hot/warm completion cache.
    """
    if settings.executor_mode.lower() == "echo":
        return _maybe_wrap_cache(EchoExecutor(), settings)

    primary_breaker = _breaker(settings, settings.llm_provider_name)
    _breaker_registry[settings.llm_provider_name] = primary_breaker

    providers: list[Provider] = [
        Provider(
            name=settings.llm_provider_name,
            executor=OpenAICompatExecutor(
                name=settings.llm_provider_name,
                base_url=settings.llm_base_url,
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
                executor=OpenAICompatExecutor(
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
