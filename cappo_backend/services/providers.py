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
    ProviderExecutionError,
    ResilientExecutor,
    TerminalExecutionError,
    VerifiedProviderUnavailableError,
    ProviderCredentialRejectedError,
    ProviderPolicyRejectedError,
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
        api_key = self._api_key() if callable(self._api_key) else self._api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = self._http().post("/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                resp_text = exc.response.text.lower()
                if any(x in resp_text for x in ("key", "token", "auth", "credential")):
                    raise ProviderCredentialRejectedError(
                        f"Authority Denied (403): Provider credential rejected: {exc.response.text}"
                    ) from exc
                else:
                    raise ProviderPolicyRejectedError(
                        f"Authority Denied (403): Provider policy/model access rejected: {exc.response.text}"
                    ) from exc
            if exc.response.status_code == 503:
                _require_verified_503(exc.response)
                raise VerifiedProviderUnavailableError(
                    f"{self.provider} returned verified HTTP 503"
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
        api_key = self._api_key() if callable(self._api_key) else self._api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = self._http().post("/api/chat", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                resp_text = exc.response.text.lower()
                if any(x in resp_text for x in ("key", "token", "auth", "credential")):
                    raise ProviderCredentialRejectedError(
                        f"Authority Denied (403): Provider credential rejected: {exc.response.text}"
                    ) from exc
                else:
                    raise ProviderPolicyRejectedError(
                        f"Authority Denied (403): Provider policy/model access rejected: {exc.response.text}"
                    ) from exc
            if exc.response.status_code == 503:
                _require_verified_503(exc.response)
                raise VerifiedProviderUnavailableError(
                    f"{self.provider} returned verified HTTP 503"
                ) from exc
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


def _require_verified_503(response: httpx.Response) -> None:
    """Convert only an authenticated 503 into an eligible failover signal."""
    from cappo_backend.config import get_settings
    from cappo_backend.security.http_signatures import (
        SignatureVerificationError,
        verify_rfc9421_response,
    )

    try:
        verify_rfc9421_response(
            status_code=response.status_code,
            headers=response.headers,
            body=response.content,
            public_key_hex=get_settings().vnp_federation_public_key,
        )
    except SignatureVerificationError as exc:
        raise ProviderExecutionError(
            f"{response.status_code} response is not an authenticated failover signal: {exc}"
        ) from exc


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
    """Resolve the configured provider base URL.

    Returns the configured LLM_BASE_URL without any topology routing.
    The OLLAMA_BASE_URL variable is read but its self-proxy semantics have been
    removed (DAN router was a prototype that has been unwired). Topology
    abstraction for local Ollama is implemented in P0-6 via OLLAMA_UPSTREAM_URL
    and OllamaEndpointResolver.
    """
    return settings.llm_base_url.rstrip("/")


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


def build_executor(settings: Settings, db: Session = None, workspace_id: str = None) -> Executor:
    """Construct the execution-layer executor from tenant keys or settings fallback."""
    if settings.executor_mode.lower() == "echo":
        return _maybe_wrap_cache(EchoExecutor(), settings)

    providers: list[Provider] = []
    
    # 1. Check Tenant Vault if DB is provided
    tenant_keys = []
    if db and workspace_id:
        from cappo_backend.models.tenant_provider_credential import TenantProviderCredential
        tenant_keys = db.query(TenantProviderCredential).filter(TenantProviderCredential.workspace_id == workspace_id).all()
        
    keys_by_provider = {k.provider.lower(): k for k in tenant_keys}
    
    from cappo_backend.security.ssrf import is_safe_url

    # Add BYOK Providers based on Tenant keys (OpenAI, Groq, HuggingFace, Ollama)
    if keys_by_provider:
        for p_name, p_record in keys_by_provider.items():
            base_url = p_record.base_url
            if base_url:
                allow_private = (p_name == "ollama")
                if not is_safe_url(base_url, allow_private=allow_private):
                    # SSRF violation, skip this provider
                    continue
            else:
                base_url = f"https://api.{p_name}.com/v1" if p_name != "ollama" else _provider_base_url(settings)
                
            p_breaker = _breaker(settings, p_name)
            _breaker_registry[p_name] = p_breaker
            def make_resolver(rec=p_record):
                def resolve():
                    if not rec.encrypted_secret:
                        return ""
                    from cappo_backend.security.vault import decrypt_secret
                    associated_data = f"{rec.workspace_id}:{rec.provider}:{rec.credential_profile}:{rec.key_version}"
                    return decrypt_secret(settings.vault_master_key, rec.encrypted_secret, associated_data)
                return resolve

            providers.append(Provider(
                name=p_name,
                executor=_provider_executor(
                    name=p_name,
                    base_url=base_url,
                    model=settings.llm_model,
                    api_key=make_resolver(),
                    timeout=settings.llm_timeout_seconds,
                ),
                breaker=p_breaker,
            ))

    # 3. If no tenant keys and no DB provided, fallback to global settings ONLY if allowed (for tests)
    if not keys_by_provider and settings.allow_legacy_global_provider_config:
        if settings.llm_provider_name:
            primary_name = settings.llm_provider_name
            p_breaker = _breaker(settings, primary_name)
            _breaker_registry[primary_name] = p_breaker
            providers.insert(0, Provider(
                name=primary_name,
                executor=_provider_executor(
                    name=primary_name,
                    base_url=_provider_base_url(settings),
                    model=settings.llm_model,
                    api_key=settings.llm_api_key,
                    timeout=settings.llm_timeout_seconds,
                ),
                breaker=p_breaker,
            ))

    # Global fallback (e.g. Server-provided Ollama) is available to all tenants,
    # but ResilientExecutor will only use it if the task's authority envelope explicitly permits it.
    if settings.llm_fallback_base_url:
        fallback_name = settings.llm_fallback_provider_name or "fallback"
        if fallback_name not in [p.name for p in providers]:
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

    # Deduplicate providers by name, keeping the first occurrence
    seen_names = set()
    deduped_providers = []
    for p in providers:
        if p.name not in seen_names:
            seen_names.add(p.name)
            deduped_providers.append(p)

    if not deduped_providers:
        from cappo_backend.services.executor import AuthorizedProviderNotConfiguredError
        class DisabledExecutor:
            def execute(self, request: dict[str, Any]) -> dict[str, Any]:
                raise AuthorizedProviderNotConfiguredError("No configured providers are available in this workspace")
        deduped_providers = [
            Provider(
                name="disabled",
                executor=DisabledExecutor(),
                breaker=_breaker(settings, "disabled"),
            )
        ]

    return _maybe_wrap_cache(ResilientExecutor(deduped_providers), settings)
