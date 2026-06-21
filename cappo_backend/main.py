"""CAPPO runtime FastAPI application.

Middleware stack order (migration note §7), outermost → innermost:
    1. RequestLogging   — structured access log + X-Request-ID for every request
    2. CORS             — preflight handled before auth (no key on OPTIONS)
    3. Auth             — authentication only (X-API-Key); NOT authority
    4. Budget / kill-switch (402) and EI / LAW 0 (403) are enforced *inside* the
       governed pipeline route, with 402 taking precedence over 403.

There is **no** public-path bypass for ``/v1/exec`` — it is authenticated here
and authority-checked (EI/LAW 0) downstream in the orchestrator (Option A).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cappo_backend.api.routers.admin_router import router as admin_router
from cappo_backend.api.routers.audit_router import router as audit_router
from cappo_backend.api.routers.benchmarks_router import router as benchmarks_router
from cappo_backend.api.routers.exec_router import router as exec_router
from cappo_backend.api.routers.governance_v2_router import router as governance_v2_router
from cappo_backend.api.routers.gpc_router import router as gpc_router
from cappo_backend.api.routers.license_router import router as license_router
from cappo_backend.api.routers.platform_router import router as platform_router
from cappo_backend.api.routers.x402_router import router as x402_router
from cappo_backend.config import Settings, get_settings
from cappo_backend.observability.logging import configure_logging
from cappo_backend.observability.middleware import RequestLoggingMiddleware
from cappo_backend.security.auth_middleware import AuthMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_production()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Accepts explicit settings for testability."""
    settings = settings or get_settings()

    app = FastAPI(title="CAPPO Runtime", version="0.1.0", lifespan=lifespan)

    from cappo_backend.services.x402_payment import X402FreemiumASGI, get_x402_manager
    x402_manager = get_x402_manager(settings)
    if x402_manager.is_enabled:
        app.add_middleware(
            X402FreemiumASGI,
            server=x402_manager.server,
            routes=x402_manager.routes,
            settings=settings,
        )

    # add_middleware adds outermost-last, so register innermost first.
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-Wallet-Address", "X-Payment"],
        expose_headers=[
            "X-Request-ID",
            "X-402-Version",
            "X-402-Chain",
            "X-402-Recipient",
            "X-402-Token",
            "X-402-Amount",
            "X-402-Resource",
            "X-402-App-Id",
        ],
    )
    app.add_middleware(RequestLoggingMiddleware)

    from cappo_backend.adapters.legacy.router import router as legacy_adapter_router

    app.include_router(exec_router)
    app.include_router(admin_router)
    app.include_router(audit_router)
    app.include_router(governance_v2_router)
    app.include_router(license_router)
    app.include_router(legacy_adapter_router)
    app.include_router(platform_router)
    app.include_router(benchmarks_router)
    app.include_router(gpc_router)
    app.include_router(x402_router)

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    import re

    from fastapi.openapi.utils import get_openapi
    
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
            
        openapi_schema = get_openapi(
            title="CAPPO Runtime",
            version="0.1.0",
            description="CAPPO Execution Engine with X402 Monetization",
            routes=app.routes,
        )
        
        if x402_manager.is_enabled:
            for route_key, route_config in x402_manager.routes.items():
                try:
                    method, path = route_key.split(" ", 1)
                    method = method.lower()
                    openapi_path = re.sub(r':([a-zA-Z0-9_]+)', r'{\1}', path)
                    
                    if openapi_path in openapi_schema.get("paths", {}):
                        if method in openapi_schema["paths"][openapi_path]:
                            operation = openapi_schema["paths"][openapi_path][method]
                            
                            if "responses" not in operation:
                                operation["responses"] = {}
                            operation["responses"]["402"] = {
                                "description": "Payment Required via X402 Protocol"
                            }
                            
                            if "parameters" not in operation:
                                operation["parameters"] = []
                            operation["parameters"].append({
                                "name": "X-Wallet-Address",
                                "in": "header",
                                "required": False,
                                "schema": {"type": "string"},
                                "description": "EVM Wallet Address for Freemium Rate Limiting"
                            })
                            
                            if route_config.accepts:
                                price = route_config.accepts[0].price
                                pay_to = route_config.accepts[0].pay_to
                                operation["x-402-payment"] = {
                                    "price": price,
                                    "pay_to": pay_to,
                                    "description": route_config.description
                                }
                except Exception:
                    continue
                    
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi

    return app


app = create_app()
