"""Middleware to extract physical connection info from trusted transport boundaries (e.g. UDS/VSOCK)."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cappo_backend.services.active_verification import _trusted_connection_info

class PhysicalBoundaryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        client = request.scope.get("client")
        info = {}
        
        if client:
            host, port = client
            if host == "unix" or str(host).startswith("/"):
                info = {"substrate_hint": "uds", "instance_hint": str(host)}
            else:
                info = {"substrate_hint": "tcp", "instance_hint": f"{host}:{port}"}
                
        # Set it directly using contextvars token.
        token = _trusted_connection_info.set(info)
        try:
            return await call_next(request)
        finally:
            _trusted_connection_info.reset(token)
