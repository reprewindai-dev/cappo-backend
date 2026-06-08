"""Execution-layer adapter.

The migration note (§5) keeps tenant resolution, RLS, conversation memory and
circuit-breaker resilience as execution-layer components *invoked by* the
governed path — not a standalone ungoverned route. Phase 1 defines the boundary
via a small :class:`Executor` protocol so the real provider clients can be wired
in later without changing the orchestrator.

A deterministic :class:`EchoExecutor` is provided for tests and local dev.
"""

from __future__ import annotations

from typing import Any, Protocol


class Executor(Protocol):
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...


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
