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
import httpx



class Executor(Protocol):
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...


class ProviderExecutionError(RuntimeError):
    """Raised when an external LLM/tool provider execution fails."""


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


class HTTPExecutor:
    """OpenAI-compatible HTTP client executor for production integrations."""

    def __init__(self, api_url: str, api_key: str | None = None, model: str = "default-model") -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.model = model

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        prompt = request.get("prompt", "")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            with httpx.Client() as client:
                resp = client.post(self.api_url, json=payload, headers=headers, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return {
                    "response": choice,
                    "model": self.model,
                    "provider": "http-provider",
                    "tokens": usage.get("total_tokens", len(choice.split())),
                }
        except Exception as exc:
            raise ProviderExecutionError(f"External provider call failed: {exc}") from exc

