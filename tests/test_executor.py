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
from cappo_backend.services.executor import ExecutorUnavailableError, TerminalExecutionError

def test_http_executor_failure_403_terminal() -> None:
    executor = ResilientExecutor(
        api_url="https://api.groq.com/openai/v1/chat/completions",
        api_key="mock-key",
    )

    # 403 should raise TerminalExecutionError immediately
    req = Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    res_403 = Response(403, request=req)
    http_error = HTTPStatusError("403 Forbidden", request=req, response=res_403)
    
    with patch("httpx.Client.post", side_effect=http_error):
        with pytest.raises(TerminalExecutionError, match="Authority Denied \\(403\\)"):
            executor.execute({"prompt": "hi", "pgl_id": "test-user-id"})

def test_http_executor_failure_503_fallback() -> None:
    # Set up executor with multiple providers to prove it fails over or raises Unavailable
    executor = ResilientExecutor(
        api_url="https://api.groq.com/openai/v1/chat/completions",
        api_key="mock-key",
    )

    req = Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    res_503 = Response(503, request=req)
    http_error = HTTPStatusError("503 Service Unavailable", request=req, response=res_503)

    with patch("httpx.Client.post", side_effect=http_error):
        with pytest.raises(ExecutorUnavailableError, match="all providers unavailable"):
            executor.execute({"prompt": "hi", "pgl_id": "test-user-id"})

    with patch("httpx.Client.post", side_effect=Exception("Connection refused")):
        with pytest.raises(ExecutorUnavailableError, match="all providers unavailable"):
            executor.execute({"prompt": "hi", "pgl_id": "test-user-id"})
