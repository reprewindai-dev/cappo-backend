import os
from typing import Any, Dict

import httpx


class PageIndexMCPClient:
    """
    Connects to the PageIndex MCP server to execute tools like 'pageindex_query'.
    """
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.pageindex.ai/mcp"):
        self.api_key = api_key or os.environ.get("PAGEINDEX_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes an MCP tool call against the PageIndex server.
        """
        if not self.api_key:
            raise ValueError("PAGEINDEX_API_KEY is not set.")
        if not tool_name or len(tool_name) > 128:
            raise ValueError("tool_name must be between 1 and 128 characters")
        if len(arguments) > 64:
            raise ValueError("arguments must contain at most 64 fields")

        payload = {
            "jsonrpc": "2.0",
            "id": "veklom-1",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(self.base_url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("PageIndex MCP returned a non-object response")
            if data.get("error") is not None:
                raise RuntimeError(f"PageIndex MCP error: {data['error']}")
            if not isinstance(data.get("result"), dict):
                raise RuntimeError("PageIndex MCP response did not contain a result")
            return data
