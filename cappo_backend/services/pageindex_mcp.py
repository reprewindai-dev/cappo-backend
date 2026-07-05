import os
import httpx
from typing import Any, Dict

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
            
            # The MCP server returns a JSON-RPC response
            return response.json()
