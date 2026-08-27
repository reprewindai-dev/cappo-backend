"""Unit tests for the ResilientExecutor service (Task B3)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import HTTPStatusError, Request, Response

from cappo_backend.services.circuit_breaker import CircuitBreaker
from cappo_backend.services.executor import (
    ExecutorUnavailableError,
    Provider,
    ResilientExecutor,
    TerminalExecutionError,
    VerifiedProviderUnavailableError,
)


def test_http_executor_success() -> None:
    executor = ResilientExecutor(
        api_url="https://api.groq.com/openai/v1/chat/completions",
        api_key="mock-key",
        model="llama3-8b-8192",
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello! I am LLM.",
                }
            }
        ],
        "usage": {
            "total_tokens": 12,
        },
    }

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        result = executor.execute({"prompt": "hi", "pgl_id": "test-user-id"})
        
        # Verify result format
        assert result["response"] == "Hello! I am LLM."
        assert result["model"] == "llama3-8b-8192"
        assert result["provider"] == "http-provider"
        assert result["tokens"] == 12

        # Verify call arguments
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["model"] == "llama3-8b-8192"
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["headers"]["Authorization"] == "Bearer mock-key"


def test_primary_success_records_a_provider_attempt() -> None:
    primary = MagicMock()
    primary.execute.return_value = {"response": "primary", "provider": "primary"}
    executor = ResilientExecutor(
        providers=[Provider("primary", primary, CircuitBreaker())]
    )

    result = executor.execute(
        {
            "prompt": "hello",
            "authority_envelope": {"allowed_provider_set": ["primary"]},
        }
    )

    assert result["attempts"] == [
        {
            "attempt_id": result["attempts"][0]["attempt_id"],
            "provider_id": "primary",
            "outcome": "succeeded",
        }
    ]


def test_verified_503_does_not_fail_over_without_authorized_provider_set() -> None:
    primary = MagicMock()
    primary.execute.side_effect = VerifiedProviderUnavailableError("verified primary 503")
    fallback = MagicMock()
    fallback.execute.return_value = {"response": "must not run"}
    executor = ResilientExecutor(
        providers=[
            Provider("primary", primary, CircuitBreaker()),
            Provider("fallback", fallback, CircuitBreaker()),
        ]
    )

    with pytest.raises(ExecutorUnavailableError, match="authorized provider set"):
        executor.execute({"prompt": "hello"})

    primary.execute.assert_called_once()
    fallback.execute.assert_not_called()


def test_verified_503_fails_over_only_inside_signed_provider_set() -> None:
    primary = MagicMock()
    primary.execute.side_effect = VerifiedProviderUnavailableError("verified primary 503")
    fallback = MagicMock()
    fallback.execute.return_value = {"response": "fallback", "provider": "fallback"}
    executor = ResilientExecutor(
        providers=[
            Provider("primary", primary, CircuitBreaker()),
            Provider("fallback", fallback, CircuitBreaker()),
        ]
    )
    request = {
        "prompt": "hello",
        "authority_envelope": {
            "execution_id": "exec-1",
            "authority_epoch": 7,
            "allowed_provider_set": ["primary", "fallback"],
        },
    }

    result = executor.execute(request)

    assert result["response"] == "fallback"
    assert [attempt["provider_id"] for attempt in result["attempts"]] == ["primary", "fallback"]
    assert result["attempts"][0]["outcome"] == "verified_unavailable"
    primary.execute.assert_called_once_with(request)
    fallback.execute.assert_called_once_with(request)


def test_open_primary_circuit_does_not_authorize_a_fallback_attempt() -> None:
    primary_breaker = CircuitBreaker(failure_threshold=1)
    with pytest.raises(RuntimeError):
        primary_breaker.compute(lambda: (_ for _ in ()).throw(RuntimeError("down")))
    primary = MagicMock()
    fallback = MagicMock()
    executor = ResilientExecutor(
        providers=[
            Provider("primary", primary, primary_breaker),
            Provider("fallback", fallback, CircuitBreaker()),
        ]
    )

    with pytest.raises(ExecutorUnavailableError, match="circuit open"):
        executor.execute(
            {"authority_envelope": {"allowed_provider_set": ["primary", "fallback"]}}
        )

    primary.execute.assert_not_called()
    fallback.execute.assert_not_called()

def test_http_executor_failure_403_terminal() -> None:
    # Set up executor with multiple providers to prove 403 halts fallback
    req = Request("POST", "https://api.primary.com/v1/chat")
    res_403 = Response(403, request=req)
    http_error = HTTPStatusError("403 Forbidden", request=req, response=res_403)
    
    mock_fail_executor = MagicMock()
    mock_fail_executor.execute.side_effect = TerminalExecutionError(f"Authority Denied (403): {http_error}")
    
    mock_success_executor = MagicMock()
    mock_success_executor.execute.return_value = {
        "response": "Fallback Success",
    }

    executor = ResilientExecutor(
        providers=[
            Provider(name="primary", executor=mock_fail_executor, breaker=CircuitBreaker()),
            Provider(name="fallback", executor=mock_success_executor, breaker=CircuitBreaker()),
        ]
    )
    
    with pytest.raises(TerminalExecutionError, match="Authority Denied \\(403\\)"):
        executor.execute({"prompt": "hi", "pgl_id": "test-user-id"})

    # Verify Provider A was called, Provider B was UNTOUCHED
    mock_fail_executor.execute.assert_called_once()
    mock_success_executor.execute.assert_not_called()

def test_http_executor_verified_503_fallback() -> None:
    # Set up executor with multiple providers to prove it fails over and succeeds
    # preserving the execution context
    mock_fail_executor = MagicMock()
    mock_fail_executor.execute.side_effect = VerifiedProviderUnavailableError("verified 503")

    mock_success_executor = MagicMock()
    mock_success_executor.execute.return_value = {
        "response": "Fallback Success",
        "model": "fallback-model",
        "provider": "fallback-provider",
        "tokens": 42,
    }

    executor = ResilientExecutor(
        providers=[
            Provider(name="primary", executor=mock_fail_executor, breaker=CircuitBreaker()),
            Provider(name="fallback", executor=mock_success_executor, breaker=CircuitBreaker()),
        ]
    )

    request_context = {
        "prompt": "hi",
        "pgl_id": "test-user-id",
        "capability_id": "cap_123",
        "execution_id": "exec_456",
        "grant_id": "grant_789",
        "policy_hash": "hash_abc",
        "actor_id": "actor_xyz",
        "authority_envelope": {
            "execution_id": "exec_456",
            "allowed_provider_set": ["primary", "fallback"],
        },
    }

    # Execution should succeed by falling back to the second provider
    result = executor.execute(request_context)

    assert result["response"] == "Fallback Success"
    assert result["provider"] == "fallback-provider"

    # Verify both were called with exactly the SAME context
    mock_fail_executor.execute.assert_called_once_with(request_context)
    mock_success_executor.execute.assert_called_once_with(request_context)
