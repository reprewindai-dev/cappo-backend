"""Real execution-layer providers.

Provider clients stay behind the governed execution boundary. OpenAI-compatible
providers use ``POST /chat/completions``. Ollama is intentionally different:
its model lifecycle controls are exposed by the native ``POST /api/chat`` API,
so CAPPO uses a dedicated native adapter whenever the configured provider name
is ``ollama``.
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
    ProviderCredentialRejectedError,
    ProviderExecutionError,
    ProviderPolicyRejectedError,
    ProviderRateLimitedError,
    ResilientExecutor,
    VerifiedProviderUnavailableError,
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
        host_header_override: str | None = None,
        app: Any = None,
    ) -> None:
        self.provider = name
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = client
        self._host_header_override = host_header_override
        self._app = app

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
        if self._host_header_override:
            headers["Host"] = self._host_header_override
        api_key = self._api_key() if callable(self._api_key) else self._api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        max_retries = 3
        backoff_factor = 1.5
        retry_delay = 1.0
        
        for attempt in range(max_retries + 1):
            try:
                response = self._http().post("/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < max_retries:
                    retry_after = exc.response.headers.get("Retry-After")
                    delay = retry_delay * (backoff_factor ** attempt)
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            pass
                    delay = min(delay, 5.0)
                    import time
                    time.sleep(delay)
                    continue
                
                if exc.response.status_code == 429:
                    raise ProviderRateLimitedError(
                        f"Provider {self.provider} rate limited: {exc.response.text}",
                        retry_after=exc.response.headers.get("Retry-After")
                    ) from exc
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
            self._client = get_shared_http_client(self._base_url, self._timeout, self._app)
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
        host_header_override: str | None = None,
        app: Any = None,
        is_local: bool = False,
        local_ollama_enabled: bool = False,
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
        self._host_header_override = host_header_override
        self._app = app
        self._is_local = is_local
        self._local_ollama_enabled = local_ollama_enabled
        self._bypass_local_raise = False

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._is_local and not getattr(self, "_bypass_local_raise", False):
            from cappo_backend.services.executor import LocalAuthorizerUnavailableError
            if not self._local_ollama_enabled:
                raise LocalAuthorizerUnavailableError("Local Ollama is disabled in settings.")
            raise LocalAuthorizerUnavailableError("Local authorizer is unavailable. Fail closed.")

        # Determine keep_alive dynamically
        keep_alive = 0
        if self._is_local:
            keep_alive = 300
            if self._app and hasattr(self._app, "state") and hasattr(self._app.state, "settings"):
                settings = self._app.state.settings
                keep_alive = getattr(settings, "ollama_keep_alive", 300)
            
            # 1. Memory pressure check
            try:
                import psutil
                if psutil.virtual_memory().percent > 85.0:
                    keep_alive = 0
            except Exception:
                pass
            
            # 2. Idle check (recent demand)
            if self._app and hasattr(self._app, "state"):
                import time
                now = time.time()
                last_time = getattr(self._app.state, "last_ollama_request_time", None)
                self._app.state.last_ollama_request_time = now
                if last_time is not None:
                    if now - last_time > keep_alive:
                        keep_alive = 0
            
            # 3. Kill switch / revocation check (drain)
            if self._app and hasattr(self._app, "state") and getattr(self._app.state, "drain_active", False):
                keep_alive = 0

        prompt = request.get("prompt", "")
        payload: dict[str, Any] = {
            "model": request.get("model") or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": keep_alive,
        }
        options: dict[str, Any] = {}
        if "temperature" in request:
            options["temperature"] = request["temperature"]
        if "max_tokens" in request:
            options["num_predict"] = request["max_tokens"]
        if options:
            payload["options"] = options

        headers = {"Content-Type": "application/json"}
        if self._host_header_override:
            headers["Host"] = self._host_header_override
        api_key = self._api_key() if callable(self._api_key) else self._api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        max_retries = 3
        backoff_factor = 1.5
        retry_delay = 1.0
        
        for attempt in range(max_retries + 1):
            try:
                response = self._http().post("/api/chat", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < max_retries:
                    retry_after = exc.response.headers.get("Retry-After")
                    delay = retry_delay * (backoff_factor ** attempt)
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            pass
                    delay = min(delay, 5.0)
                    import time
                    time.sleep(delay)
                    continue
                
                if exc.response.status_code == 429:
                    raise ProviderRateLimitedError(
                        f"Provider {self.provider} rate limited: {exc.response.text}",
                        retry_after=exc.response.headers.get("Retry-After")
                    ) from exc
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
            self._client = get_shared_http_client(self._base_url, self._timeout, self._app)
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


_shared_http_clients: dict[str, httpx.Client] = {}
_test_hot_cache = None
_test_warm_cache = None

def get_shared_http_client(base_url: str, timeout: float, app: Any = None) -> httpx.Client:
    """Get or create a shared, app-scoped HTTP client for the given base URL."""
    key = f"{base_url}:{timeout}"
    if app is not None and hasattr(app, "state"):
        if not hasattr(app.state, "http_clients"):
            app.state.http_clients = {}
        if key not in app.state.http_clients:
            app.state.http_clients[key] = httpx.Client(
                base_url=base_url,
                timeout=timeout,
                follow_redirects=False,
            )
        return app.state.http_clients[key]
    
    global _shared_http_clients
    if key not in _shared_http_clients:
        _shared_http_clients[key] = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=False,
        )
    return _shared_http_clients[key]

def _get_hot_cache(settings: Settings, app: Any = None) -> HotCache:
    if app is not None and hasattr(app, "state"):
        if not hasattr(app.state, "hot_cache"):
            app.state.hot_cache = HotCache(max_size=settings.hot_cache_max_size, ttl=settings.cache_ttl_seconds)
        return app.state.hot_cache
    return HotCache(max_size=settings.hot_cache_max_size, ttl=settings.cache_ttl_seconds)

def _get_warm_cache(settings: Settings, app: Any = None) -> WarmCache:
    if app is not None and hasattr(app, "state"):
        if not hasattr(app.state, "warm_cache"):
            app.state.warm_cache = _build_warm_cache(settings)
        return app.state.warm_cache
    return _build_warm_cache(settings)

def _maybe_wrap_cache(executor: Executor, settings: Settings, app: Any = None) -> Executor:
    if not settings.cache_enabled:
        return executor
    hot = _get_hot_cache(settings, app)
    warm = _get_warm_cache(settings, app)
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
    host_header_override: str | None = None,
    app: Any = None,
    is_local: bool = False,
    local_ollama_enabled: bool = False,
) -> Executor:
    if name.lower() == "ollama":
        return OllamaExecutor(
            name=name,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            host_header_override=host_header_override,
            app=app,
            is_local=is_local,
            local_ollama_enabled=local_ollama_enabled,
        )
    return OpenAICompatExecutor(
        name=name,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        host_header_override=host_header_override,
        app=app,
    )


def OllamaEndpointResolver(upstream_url: str, settings: Settings, allow_disabled: bool = False) -> str:
    """Resolve the local Ollama upstream URL under VEKLOM_MANAGED_LOCAL_OLLAMA constraints."""
    if not settings.local_ollama_enabled and not allow_disabled:
        raise ValueError("Local Ollama is not enabled in settings.")
    if not upstream_url:
        raise ValueError("OLLAMA_UPSTREAM_URL is empty.")
    
    from cappo_backend.security.ssrf import EndpointClass, validate_endpoint
    validated_url, _ = validate_endpoint(upstream_url, EndpointClass.VEKLOM_MANAGED_LOCAL_OLLAMA)
    return validated_url


def build_executor(settings: Settings, db: Session = None, workspace_id: str = None, app: Any = None) -> Executor:
    """Construct the execution-layer executor from tenant keys or settings fallback."""
    if settings.executor_mode.lower() == "echo":
        return _maybe_wrap_cache(EchoExecutor(), settings, app=app)

    providers: list[Provider] = []
    
    # 1. Check Tenant Vault if DB is provided
    tenant_keys = []
    if db and workspace_id:
        from cappo_backend.models.tenant_provider_credential import TenantProviderCredential
        tenant_keys = db.query(TenantProviderCredential).filter(TenantProviderCredential.workspace_id == workspace_id).all()
        
    keys_by_provider = {k.provider.lower(): k for k in tenant_keys}
    
    from cappo_backend.security.ssrf import EndpointClass, validate_endpoint

    # Add BYOK Providers based on Tenant keys (OpenAI, Groq, HuggingFace, Ollama)
    if keys_by_provider:
        for p_name, p_record in keys_by_provider.items():
            base_url = p_record.base_url
            host_header_override = None
            if base_url:
                endpoint_class = EndpointClass.TENANT_MANAGED_OLLAMA if p_name == "ollama" else EndpointClass.EXTERNAL_PROVIDER
                try:
                    base_url, host_header_override = validate_endpoint(base_url, endpoint_class)
                except Exception:
                    # SSRF violation, skip this provider
                    continue
            else:
                if p_name == "ollama":
                    base_url = _provider_base_url(settings)
                    try:
                        base_url, host_header_override = validate_endpoint(base_url, EndpointClass.VEKLOM_MANAGED_LOCAL_OLLAMA)
                    except Exception:
                        continue
                else:
                    base_url = f"https://api.{p_name}.com/v1"
                    try:
                        base_url, host_header_override = validate_endpoint(base_url, EndpointClass.EXTERNAL_PROVIDER)
                    except Exception:
                        continue
                
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
                    host_header_override=host_header_override,
                    app=app,
                ),
                breaker=p_breaker,
            ))

    # 3. If no tenant keys and no DB provided, fallback to global settings ONLY if allowed (for tests)
    if not keys_by_provider and settings.allow_legacy_global_provider_config:
        if settings.llm_provider_name:
            primary_name = settings.llm_provider_name
            p_breaker = _breaker(settings, primary_name)
            _breaker_registry[primary_name] = p_breaker
            url = None
            host_header_override = None
            is_local = (primary_name == "ollama")
            
            if is_local:
                try:
                    url = OllamaEndpointResolver(settings.ollama_upstream_url or settings.llm_base_url, settings, allow_disabled=True)
                    url, host_header_override = validate_endpoint(url, EndpointClass.VEKLOM_MANAGED_LOCAL_OLLAMA)
                except Exception:
                    url = None
            else:
                url = _provider_base_url(settings)
                try:
                    url, host_header_override = validate_endpoint(url, EndpointClass.EXTERNAL_PROVIDER)
                except Exception:
                    url = None
                    
            if url:
                providers.insert(0, Provider(
                    name=primary_name,
                    executor=_provider_executor(
                        name=primary_name,
                        base_url=url,
                        model=settings.llm_model,
                        api_key=settings.llm_api_key,
                        timeout=settings.llm_timeout_seconds,
                        host_header_override=host_header_override,
                        app=app,
                        is_local=is_local,
                        local_ollama_enabled=settings.local_ollama_enabled,
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
            url = None
            host_header_override = None
            is_local = (fallback_name == "ollama")
            
            if is_local:
                try:
                    url = OllamaEndpointResolver(settings.ollama_upstream_url or settings.llm_fallback_base_url, settings, allow_disabled=True)
                    url, host_header_override = validate_endpoint(url, EndpointClass.VEKLOM_MANAGED_LOCAL_OLLAMA)
                except Exception:
                    url = None
            else:
                url = settings.llm_fallback_base_url
                try:
                    url, host_header_override = validate_endpoint(url, EndpointClass.EXTERNAL_PROVIDER)
                except Exception:
                    url = None
                    
            if url:
                providers.append(
                    Provider(
                        name=fallback_name,
                        executor=_provider_executor(
                            name=fallback_name,
                            base_url=url,
                            model=settings.llm_fallback_model or settings.llm_model,
                            api_key=settings.llm_fallback_api_key or None,
                            timeout=settings.llm_timeout_seconds,
                            host_header_override=host_header_override,
                            app=app,
                            is_local=is_local,
                            local_ollama_enabled=settings.local_ollama_enabled,
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

    return _maybe_wrap_cache(ResilientExecutor(deduped_providers), settings, app=app)
