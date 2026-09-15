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
            # client is typically (host, port)
            # For AF_HYPERV, host is a GUID (VM ID).
            # For VSOCK, host is an integer CID.
            host, port = client
            
            import re
            is_guid = isinstance(host, str) and re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', host)
            is_vsock_cid = isinstance(host, int) or (isinstance(host, str) and host.isdigit())

            if is_guid:
                info = {"substrate_hint": "hyper-v", "instance_hint": host}
            elif is_vsock_cid and host != "unix" and not str(host).startswith("/"):
                # Simple heuristic for VSOCK CID
                info = {"substrate_hint": "vsock", "instance_hint": str(host)}
            elif host == "unix" or str(host).startswith("/"):
                info = {"substrate_hint": "uds", "instance_hint": str(host)}
            else:
                info = {"substrate_hint": "tcp", "instance_hint": f"{host}:{port}"}
                
        # To prevent application override, we strictly read from ASGI scope/socket layer,
        # never from user-supplied HTTP headers (which could be spoofed unless stripped by trusted proxy).
        if "hcs_compute_system_id" in request.scope:
            info["compute_system_id"] = request.scope["hcs_compute_system_id"]
                
        # Set it directly using contextvars token.
        token = _trusted_connection_info.set(info)
        try:
            return await call_next(request)
        finally:
            _trusted_connection_info.reset(token)
