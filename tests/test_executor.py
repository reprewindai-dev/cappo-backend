"""Unit tests for the ResilientExecutor service (Task B3)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cappo_backend.services.executor import ExecutorUnavailableError, ResilientExecutor


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


from httpx import HTTPStatusError, Request, Response
from cappo_backend.services.executor import ExecutorUnavailableError, TerminalExecutionError, Provider
from cappo_backend.services.circuit_breaker import CircuitBreaker

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

def test_http_executor_failure_503_fallback() -> None:
    # Set up executor with multiple providers to prove it fails over and succeeds
    # preserving the execution context
    req = Request("POST", "https://api.primary.com/v1/chat")
    res_503 = Response(503, request=req)
    http_error = HTTPStatusError("503 Service Unavailable", request=req, response=res_503)

    mock_fail_executor = MagicMock()
    # Need to raise the correct error type expected by executor (Exception that triggers fallback)
    mock_fail_executor.execute.side_effect = http_error

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
        "actor_id": "actor_xyz"
    }

    # Execution should succeed by falling back to the second provider
    result = executor.execute(request_context)

    assert result["response"] == "Fallback Success"
    assert result["provider"] == "fallback-provider"

    # Verify both were called with exactly the SAME context
    mock_fail_executor.execute.assert_called_once_with(request_context)
    mock_success_executor.execute.assert_called_once_with(request_context)
