"""Authentication middleware — the earlier, separate layer before LAW 0 authority.

Migration note §4: the old ``ZeroTrustMiddleware`` enforced authentication at a
single choke point with a public-path allowlist. CAPPO keeps that shape but is
explicit that **authentication is not authority** — passing this layer only
proves *who* is calling, never *permission to execute*. EI/LAW 0 authority is
enforced separately inside the governed pipeline.

Critically, ``/v1/exec`` is **not** on the public allowlist (the old backend's
LAW 0 bypass), so every side-effecting route is authenticated here and then
authority-checked downstream.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cappo_backend.config import Settings

# Non-side-effecting, safe-to-expose paths. Note: /v1/exec is deliberately absent.
PUBLIC_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._settings.auth_enabled:
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={"error": "AUTHENTICATION_REQUIRED", "detail": "missing X-API-Key"},
            )
        if api_key not in self._settings.api_key_set:
            return JSONResponse(
                status_code=401,
                content={"error": "AUTHENTICATION_REQUIRED", "detail": "invalid X-API-Key"},
            )

        return await call_next(request)
