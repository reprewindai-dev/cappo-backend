"""Unit tests for the HTTPExecutor service (Task B3)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from cappo_backend.services.executor import HTTPExecutor, ProviderExecutionError


def test_http_executor_success() -> None:
    executor = HTTPExecutor(
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
        result = executor.execute({"prompt": "hi"})
        
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


def test_http_executor_failure() -> None:
    executor = HTTPExecutor(
        api_url="https://api.groq.com/openai/v1/chat/completions",
        api_key="mock-key",
    )

    with patch("httpx.Client.post", side_effect=Exception("Connection refused")):
        with pytest.raises(ProviderExecutionError, match="External provider call failed"):
            executor.execute({"prompt": "hi"})
            
        # Or if it returns an HTTP error status code:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("Internal Server Error")
        
    with patch("httpx.Client.post", return_value=mock_response):
        with pytest.raises(ProviderExecutionError, match="Internal Server Error"):
            executor.execute({"prompt": "hi"})
