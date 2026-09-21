"""Authentication middleware — authentication precedes LAW 0 authority.

Passing this layer proves caller identity only; it never grants execution
authority. Authenticated routes receive a non-secret principal identifier in the
ASGI scope so downstream resources can bind ownership without retaining bearer
credentials or API keys.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cappo_backend.config import Settings
from cappo_backend.db.session import SessionLocal
from cappo_backend.models.capability_mount import CapabilityMount

PUBLIC_PATHS = frozenset(
    {
        "/",
        "/favicon.ico",
        "/robots.txt",
        "/health",
        "/runtime/identity",
        "/runtime/probe/cappo",
        "/runtime/probe/pgl",
        "/runtime/observations",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/v1/license/validate",
        "/v1/license/activate",
        "/v1/vnp/metrics",
        "/.well-known/x402",
        "/.well-known/x402.json",
        "/.well-known/capability-beacon-keys",
        "/.well-known/capability-beacon-keys.json",
        "/x402/bazaar",
        "/api/v1/pricing",
    }
)


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings
        self._jwt_key = None
        if settings.jwt_public_verification_key:
            try:
                key_bytes = bytes.fromhex(settings.jwt_public_verification_key)
                self._jwt_key = Ed25519PublicKey.from_public_bytes(key_bytes)
            except ValueError:
                self._jwt_key = settings.jwt_public_verification_key

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._settings.auth_enabled:
            request.scope["auth_principal"] = "auth-disabled"
            request.scope["auth_workspace"] = request.headers.get("X-Workspace-ID", "default-local-workspace")
            request.scope["caller_spiffe_id"] = "spiffe://example.org/workload/cappo-backend"
            return await call_next(request)

        path = request.url.path
        if (
            path in PUBLIC_PATHS
            or path.startswith("/api/v1/execution/keys/")
            or path.startswith("/api/v1/reconcile/")
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        auth_header = request.headers.get("Authorization")
        token = api_key or (auth_header.removeprefix("Bearer ").strip() if auth_header else None)
        if not token:
            return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)

        if token.startswith("vlm_"):
            mount_id, separator, secret = token[4:].rpartition(".")
            if not mount_id or not separator or not secret:
                return JSONResponse({"error": "HOLDER_CREDENTIAL_INVALID"}, status_code=401)

            db = SessionLocal()
            try:
                row = db.get(CapabilityMount, mount_id)
            finally:
                db.close()

            if (
                row is None
                or not row.holder_secret_hash
                or not hmac.compare_digest(
                    hashlib.sha256(secret.encode()).hexdigest(),
                    row.holder_secret_hash,
                )
            ):
                return JSONResponse({"error": "HOLDER_CREDENTIAL_INVALID"}, status_code=401)

            request.scope["auth_principal"] = f"mount-holder:{mount_id}"
            request.scope["auth_workspace"] = row.owner_workspace
            request.scope["mount_holder_id"] = mount_id
            if row.terminated:
                if request.method != "GET":
                    return JSONResponse({"error": "HOLDER_CREDENTIAL_REVOKED"}, status_code=401)
                request.scope["mount_holder_state"] = "terminated"
                return await call_next(request)
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                if request.method != "GET":
                    return JSONResponse({"error": "HOLDER_CREDENTIAL_EXPIRED"}, status_code=401)
                request.scope["mount_holder_state"] = "expired"
                return await call_next(request)
        elif token.startswith("eyJ"):
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
                subject = str(payload.get("sub") or _token_fingerprint(token))
                issuer = str(payload.get("iss") or self._settings.jwt_issuer or "unknown")
                request.scope["auth_principal"] = f"jwt:{issuer}:{subject}"
                workspace = payload.get("workspace_id") or payload.get("workspace") or payload.get(
                    "tenant_id"
                )
                if workspace and str(workspace).strip():
                    request.scope["auth_workspace"] = str(workspace).strip()
                # If JWT carries no workspace claim, auth_workspace is absent.
                # Tenant-sensitive routes will fail with WORKSPACE_CONTEXT_MISSING.
            except jwt.ExpiredSignatureError:
                return JSONResponse({"error": "TOKEN_EXPIRED"}, status_code=401)
            except jwt.InvalidTokenError:
                return JSONResponse({"error": "INVALID_TOKEN"}, status_code=401)
        else:
            if token not in self._settings.api_key_set:
                return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)
            request.scope["auth_principal"] = f"api-key:{_token_fingerprint(token)}"
            # API keys do NOT resolve their workspace from the X-Workspace-ID header.
            # The workspace must be bound server-side to the credential. Until per-key
            # workspace binding is implemented, auth_workspace is intentionally absent
            # so tenant-sensitive routes fail closed (WORKSPACE_CONTEXT_MISSING) rather
            # than silently using a body-supplied "default".
            #
            # X-Workspace-ID is preserved only for future use as a membership selector
            # once server-side key→workspace binding is stored. It never creates membership.
            workspace_hint = request.headers.get("X-Workspace-ID", "").strip()
            if workspace_hint:
                request.scope["auth_workspace_hint"] = workspace_hint
            # auth_workspace is NOT set here.

        operator_key = request.headers.get("x-uacp-internal-key")
        if operator_key and operator_key in self._settings.api_key_set:
            request.scope["cappo_internal_operator_valid"] = True

        return await call_next(request)
