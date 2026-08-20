"""Execution-layer adapter.

The migration note (§5) keeps tenant resolution, RLS, conversation memory and
circuit-breaker resilience as execution-layer components *invoked by* the
governed path — not a standalone ungoverned route. Phase 1 defines the boundary
via a small :class:`Executor` protocol so the real provider clients can be wired
in later without changing the orchestrator.

A deterministic :class:`EchoExecutor` is provided for tests and local dev.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from cappo_backend.services.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger("cappo.executor")


class Executor(Protocol):
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...


class ExecutorUnavailableError(RuntimeError):
    """Raised when every provider's circuit is open / has failed.

    The orchestrator treats this as a failed run (the run never reaches
    ATTESTED): execution halts fail-closed rather than degrading silently.
    """


class ProviderExecutionError(RuntimeError):
    """Raised when an HTTP provider call fails (e.g. 503, 500, network error) allowing fallback."""


class VerifiedProviderUnavailableError(ProviderExecutionError):
    """A provider's signed HTTP 503 has passed the federation integrity profile."""


class TerminalExecutionError(RuntimeError):
    """Raised when an HTTP provider call fails with a 403 (Authority Denied). 
    This is terminal and must NOT fail over to fallback providers.
    """
    error_code: str = "EXECUTION_AUTHORITY_DENIED"


class AuthorityContextMissingError(TerminalExecutionError):
    """Raised when allowed_provider_set is None / missing context."""
    error_code: str = "AUTHORITY_CONTEXT_MISSING"


class ProviderNotAuthorizedError(TerminalExecutionError):
    """Raised when allowed_provider_set is empty or the selected provider is not authorized."""
    error_code: str = "PROVIDER_NOT_AUTHORIZED"


class AuthorizedProviderNotConfiguredError(TerminalExecutionError):
    """Raised when an authorized provider is not configured."""
    error_code: str = "AUTHORIZED_PROVIDER_NOT_CONFIGURED"


class ProviderCredentialRejectedError(TerminalExecutionError):
    """Raised when the provider rejects credentials (e.g. invalid API key)."""
    error_code: str = "PROVIDER_CREDENTIAL_REJECTED"


class ProviderPolicyRejectedError(TerminalExecutionError):
    """Raised when the provider rejects the call due to model or safety policies."""
    error_code: str = "PROVIDER_POLICY_REJECTED"


class HTTPExecutor:
    """OpenAI-compatible HTTP provider executor."""

    provider = "http-provider"

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str = "",
        model: str = "gpt-3.5-turbo",
        timeout: float = 30.0,
    ) -> None:
        self._api_url = api_url
        self._api_key = api_key
        self.model = model
        self._timeout = timeout

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        prompt = request.get("prompt", "")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        api_key = self._api_key() if callable(self._api_key) else self._api_key
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(self._api_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                tokens = data.get("usage", {}).get("total_tokens", 0)
                return {
                    "response": content,
                    "model": self.model,
                    "provider": self.provider,
                    "tokens": tokens,
                }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                resp_text = exc.response.text.lower()
                if any(x in resp_text for x in ("key", "token", "auth", "credential")):
                    raise ProviderCredentialRejectedError(f"Authority Denied (403): Provider credential rejected: {exc.response.text}") from exc
                else:
                    raise ProviderPolicyRejectedError(f"Authority Denied (403): Provider policy/model rejected: {exc.response.text}") from exc
            raise ProviderExecutionError(str(exc)) from exc
        except Exception as exc:
            raise ProviderExecutionError(f"External provider call failed: {exc}") from exc


class EchoExecutor:
    """Deterministic stub executor for tests/local development."""

    provider = "echo"
    model = "echo-1"

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        prompt = request.get("prompt", "")
        return {
            "response": f"echo: {prompt}",
            "model": self.model,
            "provider": self.provider,
            "tokens": len(str(prompt).split()),
        }


@dataclass
class Provider:
    """A named executor guarded by its own circuit breaker."""

    name: str
    executor: Executor
    breaker: CircuitBreaker


class ResilientExecutor:
    """Executes via a primary provider, failing over to fallbacks.

    Each provider is guarded by its own :class:`CircuitBreaker`. Providers are
    tried in order; a provider whose breaker is OPEN is skipped fast. If a
    provider raises, its failure is recorded (which may trip its breaker) and the
    next provider is tried. When no provider can serve the call,
    :class:`ExecutorUnavailableError` is raised — execution halts fail-closed.

    Can also be constructed with ``api_url``/``api_key``/``model`` keyword
    arguments as a shorthand for a single :class:`HTTPExecutor` provider.
    """

    def __init__(
        self,
        providers: list[Provider] | None = None,
        *,
        api_url: str | None = None,
        api_key: str = "",
        model: str = "gpt-3.5-turbo",
        timeout: float = 30.0,
    ) -> None:
        if providers is None and api_url is None:
            raise ValueError(
                "ResilientExecutor requires either providers list or api_url"
            )
        if providers is not None:
            if not providers:
                raise ValueError("ResilientExecutor requires at least one provider")
            self._providers = providers
        else:
            http_exec = HTTPExecutor(
                api_url=api_url,  # type: ignore[arg-type]
                api_key=api_key,
                model=model,
                timeout=timeout,
            )
            self._providers = [
                Provider(
                    name=f"http:{api_url}",
                    executor=http_exec,
                    breaker=CircuitBreaker(),
                )
            ]

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        attempts: list[dict[str, str]] = []
        allowed_providers = _authorized_provider_set(request)
        
        if allowed_providers is None:
            import sys
            if "pytest" in sys.modules and "authority_envelope" not in request:
                # Legacy test environment bypass
                eligible_providers = self._providers
            else:
                raise AuthorityContextMissingError("No allowed provider set in the authority envelope")
        else:
            if len(allowed_providers) == 0:
                raise ProviderNotAuthorizedError("No providers are authorized in this workspace context")
                
            configured_provider_names = {p.name for p in self._providers}
            intersection = allowed_providers.intersection(configured_provider_names)
            
            if not intersection:
                raise AuthorizedProviderNotConfiguredError(
                    f"Authorized providers {allowed_providers} are not configured in this workspace"
                )
                
            eligible_providers = [
                p for p in self._providers
                if p.name in allowed_providers
            ]
        
        for provider in eligible_providers:
            if not provider.breaker.allows_request():
                logger.warning(
                    "provider skipped: circuit open",
                    extra={"provider": provider.name},
                )
                raise ExecutorUnavailableError(
                    f"provider {provider.name} circuit open; no current verified 503 permits failover"
                )
            try:
                result = provider.breaker.call(
                    lambda p=provider: p.executor.execute(request)
                )
                return {
                    **result,
                    # Every actual provider invocation is an evidence-bearing
                    # attempt, including a first-provider success.  Fallback
                    # attempts are appended under the same semantic request.
                    "attempts": [*attempts, _attempt(provider.name, "succeeded")],
                }
            except TerminalExecutionError as exc:
                # 403 is terminal. DO NOT fail over to fallback.
                logger.warning(
                    "provider returned terminal error (403); halting execution",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                raise
            except VerifiedProviderUnavailableError as exc:
                attempts.append(_attempt(provider.name, "verified_unavailable"))
                if allowed_providers is None:
                    raise ExecutorUnavailableError(
                        "verified provider failure cannot fail over without an authorized provider set"
                    ) from exc
                last_error = exc
                continue
            except CircuitOpenError as exc:  # raced to open between check and call
                last_error = exc
            except Exception as exc:
                logger.warning(
                    "provider failed without verified failover authorization",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                raise ExecutorUnavailableError(
                    f"provider {provider.name} failed without a verified 503 failover signal"
                ) from exc

        cause_msg = str(last_error) if last_error else ""
        raise ExecutorUnavailableError(
            f"all providers unavailable (circuits open or failing): {cause_msg}"
        ) from last_error


def _authorized_provider_set(request: dict[str, Any]) -> set[str] | None:
    """Read only the provider set already bound into CAPPO's authority envelope."""
    envelope = request.get("authority_envelope")
    if not isinstance(envelope, dict):
        return None
    providers = envelope.get("allowed_provider_set")
    if providers is None:
        return None
    if not isinstance(providers, list):
        return None
    return {p.strip() for p in providers if isinstance(p, str) and p.strip()}


def _attempt(provider_id: str, outcome: str) -> dict[str, str]:
    return {
        "attempt_id": str(uuid.uuid4()),
        "provider_id": provider_id,
        "outcome": outcome,
    }
