"""
Amphoteric Sensing Middleware for CAPPO Runtime.

Detects the protocol environment (pH) of incoming requests.
"""

from enum import Enum
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class AmphotericProtocol(str, Enum):
    WEB_UI = "web_ui"
    WEBMCP = "webmcp"
    MCP_RPC = "mcp_rpc"
    REST_API = "rest_api"

class AmphotericSensingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        protocol = self._sense_protocol(request)
        request.state.amphoteric_protocol = protocol

        response: Response = await call_next(request)

        response.headers["X-Amphoteric-Protocol"] = protocol.value
        return response

    def _sense_protocol(self, request: Request) -> AmphotericProtocol:
        headers = request.headers
        content_type = headers.get("content-type", "").lower()
        user_agent = headers.get("user-agent", "").lower()

        if "application/json-rpc" in content_type or headers.get("x-mcp-version"):
            return AmphotericProtocol.MCP_RPC

        if headers.get("x-webmcp-enabled") == "true" or headers.get("sec-webmcp-context"):
            return AmphotericProtocol.WEBMCP

        if request.url.path.startswith("/v1") and ("mozilla" not in user_agent and "chrome" not in user_agent):
            return AmphotericProtocol.REST_API

        return AmphotericProtocol.WEB_UI
