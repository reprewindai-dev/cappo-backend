"""Adversarial tests for P0-7 (Precise authority/provider failure taxonomy)."""

from __future__ import annotations

import httpx
import pytest

from cappo_backend.services.executor import (
    ProviderRateLimitedError,
    ResilientExecutor,
)
from cappo_backend.services.providers import OpenAICompatExecutor, Provider


def test_provider_429_retries_and_raises_rate_limited() -> None:
    # Set up mock transport that returns 429
    call_count = 0
    def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, json={"error": "Too Many Requests"}, headers={"Retry-After": "2"})

    exec_inst = OpenAICompatExecutor(
        name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4",
        api_key="sk-test",
    )
    exec_inst._http()._transport = httpx.MockTransport(handler)

    # Shorten sleep for testing so it runs fast
    import time
    original_sleep = time.sleep
    sleep_calls = []
    time.sleep = lambda secs: sleep_calls.append(secs)

    try:
        with pytest.raises(ProviderRateLimitedError) as exc_info:
            exec_inst.execute({"prompt": "hi"})
        
        assert exc_info.value.retry_after == "2"
        # Total attempts: initial (0) + 3 retries = 4 calls
        assert call_count == 4
        assert len(sleep_calls) == 3
        assert sleep_calls[0] == 2.0  # mock header Retry-After is 2
    finally:
        time.sleep = original_sleep

def test_resilient_executor_does_not_failover_on_rate_limit() -> None:
    # 2 providers. The first returns 429. Since it is terminal, it must NOT failover to the second.
    def handler_1(request):
        return httpx.Response(429, json={"error": "Rate limit"})

    def handler_2(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "fallback response"}}]})

    # Use different hostnames so they get separate connection pool instances
    exec_1 = OpenAICompatExecutor(name="p1", base_url="https://api1.openai.com/v1", model="gpt-4", api_key="sk-1")
    exec_2 = OpenAICompatExecutor(name="p2", base_url="https://api2.openai.com/v1", model="gpt-4", api_key="sk-2")

    exec_1._http()._transport = httpx.MockTransport(handler_1)
    exec_2._http()._transport = httpx.MockTransport(handler_2)

    # Disable sleep for fast unit tests
    import time
    original_sleep = time.sleep
    time.sleep = lambda secs: None

    try:
        # Build ResilientExecutor
        from cappo_backend.services.circuit_breaker import CircuitBreaker
        res_exec = ResilientExecutor([
            Provider(name="p1", executor=exec_1, breaker=CircuitBreaker()),
            Provider(name="p2", executor=exec_2, breaker=CircuitBreaker()),
        ])

        with pytest.raises(ProviderRateLimitedError):
            res_exec.execute({
                "prompt": "hi",
                "authority_envelope": {"execution_id": "test", "allowed_provider_set": ["p1", "p2"]}
            })
    finally:
        time.sleep = original_sleep
