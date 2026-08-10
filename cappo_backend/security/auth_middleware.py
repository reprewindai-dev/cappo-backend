"""Authentication middleware — the earlier, separate layer before LAW 0 authority.

Migration note §4: the old ``ZeroTrustMiddleware`` enforced authentication at a
single choke point with a public-path allowlist. CAPPO keeps that shape but is
explicit that **authentication is not authority** — passing this layer only
proves *who* is calling, never *permission to execute*. EI/LAW 0 authority is
enforced separately inside the governed pipeline.

Critically, ``/v1/exec`` is **not** on the public allowlist (the old backend's
LAW 0 bypass), so every side-effecting route is either paid via x402 or
bypassed by an explicitly governed internal operator credential.
"""

from __future__ import annotations

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cappo_backend.config import Settings

# Non-side-effecting, safe-to-expose paths. Note: /v1/exec is deliberately absent.
# License endpoints use their own X-License-Admin-Key header for admin operations;
# /validate and /activate are intentionally public for veklom-byos-backend to call.
PUBLIC_PATHS = frozenset({
    "/",
    "/favicon.ico",
    "/robots.txt",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/v1/license/validate",
    "/v1/license/activate",
    "/v1/vnp/metrics",
    "/.well-known/x402",
    "/.well-known/x402.json",
    "/.well-known/capability-beacon-keys",
    "/x402/bazaar",
    "/api/v1/pricing",
})


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings
        self._jwt_key = None
        if settings.jwt_public_verification_key:
            try:
                # Try loading from hex first (Veklom ecosystem standard)
                key_bytes = bytes.fromhex(settings.jwt_public_verification_key)
                self._jwt_key = Ed25519PublicKey.from_public_bytes(key_bytes)
            except ValueError:
                # Fallback to assuming PEM
                self._jwt_key = settings.jwt_public_verification_key

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._settings.auth_enabled:
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)
            
        # Also allow anything under /.well-known/ if we decide to wildcard it
        if path.startswith("/.well-known/"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        auth_header = request.headers.get("Authorization")
        cookie_token = request.cookies.get("access_token")
        
        token = api_key or (auth_header.removeprefix("Bearer ").strip() if auth_header else cookie_token)
        if not token:
            return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)

        if token.startswith("eyJ"):
            if not self._jwt_key:
                return JSONResponse({"error": "JWT_MISCONFIGURED"}, status_code=500)
            try:
                payload = jwt.decode(
                    token,
                    self._jwt_key,
                    algorithms=[self._settings.jwt_algorithm],
                    issuer=self._settings.jwt_issuer,
                    audience=self._settings.jwt_audience,
                    options={"require": ["exp", "iss", "aud"]},
                )
                request.scope["jwt_payload"] = payload
            except jwt.ExpiredSignatureError:
                return JSONResponse({"error": "TOKEN_EXPIRED"}, status_code=401)
            except jwt.InvalidTokenError as e:
                return JSONResponse({"error": "INVALID_TOKEN", "detail": str(e)}, status_code=401)
        else:
            if token not in self._settings.api_key_set:
                return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)

        operator_key = request.headers.get("x-uacp-internal-key")
        if operator_key and operator_key in self._settings.api_key_set:
            request.scope["cappo_internal_operator_valid"] = True

        return await call_next(request)
