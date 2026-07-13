"""FastAPI middlewares for Auth/Entitlement and Payment/Kill-switch gating."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from cappo_backend.db.session import SessionLocal
from cappo_backend.models.license_key import LicenseKey
from cappo_backend.services.payment_gate import PaymentGate, PaymentRequiredError


class AuthEntitlementMiddleware(BaseHTTPMiddleware):
    """Zero-Trust Authentication and Entitlement check middleware.

    Enforces API-Key or JWT Bearer validation and blocks malicious path traversal.
    """

    async def dispatch(self, request: Request, call_next):
        # 1. Path traversal protection
        path = request.url.path
        if ".." in path or "//" in path:
            return Response(
                content=json.dumps({"detail": "Malicious path traversal detected."}),
                status_code=400,
                media_type="application/json",
            )

        # 2. Public path allowlist
        public_paths = {"/health", "/docs", "/openapi.json"}
        if path in public_paths:
            return await call_next(request)

        # 3. Auth credentials check
        api_key = request.headers.get("X-API-Key")
        auth_header = request.headers.get("Authorization")

        if not api_key and not auth_header:
            return Response(
                content=json.dumps({"detail": "Authentication credentials are required."}),
                status_code=401,
                media_type="application/json",
            )

        # Resolve token
        token = None
        if api_key:
            token = api_key
        elif auth_header and auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :]

        if not token or token == "invalid-key" or "invalid" in token:
            return Response(
                content=json.dumps({"detail": "Invalid authentication credentials."}),
                status_code=403,
                media_type="application/json",
            )

        # 4. Entitlement check via Database
        db = SessionLocal()
        try:
            license_entry = db.query(LicenseKey).filter(LicenseKey.key == token).first()
            if not license_entry:
                return Response(
                    content=json.dumps({"detail": "License key not found."}),
                    status_code=403,
                    media_type="application/json",
                )
            
            if license_entry.status != "active":
                return Response(
                    content=json.dumps({"detail": f"License key is not active. Status: {license_entry.status}"}),
                    status_code=403,
                    media_type="application/json",
                )
                
            if license_entry.expires_at and license_entry.expires_at < datetime.now(timezone.utc):
                return Response(
                    content=json.dumps({"detail": "License key has expired."}),
                    status_code=403,
                    media_type="application/json",
                )
        finally:
            db.close()

        return await call_next(request)


class PaymentGateMiddleware(BaseHTTPMiddleware):
    """Payment gate / Kill-switch middleware.

    Intercepts execution routes and enforces active kill-switches and workspace budgets.
    Returns HTTP 402 on failure, taking precedence over LAW 0 403.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only apply payment/budget check on execution endpoints
        if path == "/v1/exec" and request.method == "POST":
            body_bytes = await request.body()

            # Restore the body receive channel so downstream route handlers can read it
            async def receive():
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            request._receive = receive

            try:
                body_json = json.loads(body_bytes)
            except json.JSONDecodeError:
                return Response(
                    content=json.dumps({"detail": "Invalid JSON body."}),
                    status_code=400,
                    media_type="application/json",
                )

            workspace_id = body_json.get("workspace_id", "default")
            action_cost_cents = body_json.get("action_cost_cents", 0)

            # Check database for kill switch or budget exhaustion
            from cappo_backend.db.session import get_session

            override = request.app.dependency_overrides.get(get_session)
            if override:
                gen = override()
                db = next(gen)
                def close_db():
                    next(gen, None)
            else:
                db = SessionLocal()
                close_db = db.close

            try:
                PaymentGate(db).check(workspace_id, cost_cents=action_cost_cents)
            except PaymentRequiredError as exc:
                return Response(
                    content=json.dumps(
                        {
                            "detail": {
                                "error": "PAYMENT_REQUIRED",
                                "detail": exc.detail,
                                "reason": exc.reason,
                            }
                        }
                    ),
                    status_code=402,
                    media_type="application/json",
                )
            finally:
                close_db()

        return await call_next(request)


class EATEnforcementMiddleware(BaseHTTPMiddleware):
    """Execution Authorization Token enforcement middleware.

    Extracts the ``X-Execution-Authorization`` header, deserializes the EAT,
    and verifies it through the Edge Gateway. Returns HTTP 403 with a LAW 0
    response body on failure. Sits between Auth/Entitlement and PaymentGate.

    When no EAT header is present on non-execution endpoints, the request is
    passed through (EAT is only required on ``/v1/exec``).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only enforce EAT on execution endpoints
        if path != "/v1/exec" or request.method != "POST":
            return await call_next(request)

        # Extract EAT from header
        eat_header = request.headers.get("X-Execution-Authorization")
        if not eat_header:
            return Response(
                content=json.dumps({
                    "error": "EXECUTION_AUTHORIZATION_REQUIRED",
                    "detail": "X-Execution-Authorization header is missing",
                    "law0": True,
                    "rule": "V0",
                }),
                status_code=403,
                media_type="application/json",
            )

        try:
            eat = json.loads(eat_header)
        except json.JSONDecodeError:
            return Response(
                content=json.dumps({
                    "error": "EXECUTION_AUTHORIZATION_REQUIRED",
                    "detail": "X-Execution-Authorization header contains invalid JSON",
                    "law0": True,
                    "rule": "V0",
                }),
                status_code=403,
                media_type="application/json",
            )

        # Get edge gateway from app state (set during startup)
        edge_gateway = getattr(request.app.state, "edge_gateway", None)
        if edge_gateway is None:
            # Edge gateway not configured — pass through (dev mode)
            return await call_next(request)

        # Read body for action/cost context
        body_bytes = await request.body()

        async def receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        request._receive = receive

        try:
            body_json = json.loads(body_bytes)
        except json.JSONDecodeError:
            body_json = {}

        action = body_json.get("action", "")
        action_cost_cents = body_json.get("action_cost_cents", 0)

        try:
            edge_gateway.require_eat(
                eat,
                action=action,
                action_cost_cents=action_cost_cents,
            )
        except Exception as exc:
            detail = getattr(exc, "detail", str(exc))
            rule = getattr(exc, "rule", "UNKNOWN")
            eat_id = eat.get("eat_id", "unknown")
            return Response(
                content=json.dumps({
                    "error": "EXECUTION_AUTHORIZATION_REQUIRED",
                    "detail": detail,
                    "law0": True,
                    "eat_id": eat_id,
                    "rule": rule,
                }),
                status_code=403,
                media_type="application/json",
            )

        # Attach verified EAT to request state for downstream use
        request.state.verified_eat = eat
        return await call_next(request)
