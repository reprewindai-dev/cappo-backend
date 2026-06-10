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

from cappo_backend.services.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger("cappo.executor")


class Executor(Protocol):
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...


class ExecutorUnavailableError(RuntimeError):
    """Raised when every provider's circuit is open / has failed.

    The orchestrator treats this as a failed run (the run never reaches
    ATTESTED): execution halts fail-closed rather than degrading silently.
    """


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
    """

    def __init__(self, providers: list[Provider]) -> None:
        if not providers:
            raise ValueError("ResilientExecutor requires at least one provider")
        self._providers = providers

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

        raise ExecutorUnavailableError(
            "all providers unavailable (circuits open or failing)"
        ) from last_error
