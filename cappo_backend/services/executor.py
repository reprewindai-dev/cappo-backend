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
    """Raised when an HTTP provider call fails."""


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
        headers = {"Authorization": f"Bearer {self._api_key}"}
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
        for provider in self._providers:
            if not provider.breaker.allows_request():
                logger.warning(
                    "provider skipped: circuit open",
                    extra={"provider": provider.name},
                )
                continue
            try:
                return provider.breaker.call(
                    lambda p=provider: p.executor.execute(request)
                )
            except CircuitOpenError as exc:  # raced to open between check and call
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "provider failed; failing over",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                continue

        cause_msg = str(last_error) if last_error else ""
        raise ExecutorUnavailableError(
            f"all providers unavailable (circuits open or failing): {cause_msg}"
        ) from last_error
