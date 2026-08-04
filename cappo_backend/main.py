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

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cappo_backend.api.routers.admin_router import router as admin_router
from cappo_backend.api.routers.agents_router import router as agents_router
from cappo_backend.api.routers.audit_router import router as audit_router
from cappo_backend.api.routers.authorization_router import router as authorization_router
from cappo_backend.api.routers.benchmarks_router import router as benchmarks_router
from cappo_backend.api.routers.exec_router import router as exec_router
from cappo_backend.api.routers.governance_v2_router import router as governance_v2_router
from cappo_backend.api.routers.gpc_router import router as gpc_router
from cappo_backend.api.routers.interlink import router as interlink_router
from cappo_backend.api.routers.interlink_vnp import router as interlink_vnp_router
from cappo_backend.api.routers.license_router import router as license_router
from cappo_backend.api.routers.mcp import router as mcp_router
from cappo_backend.api.routers.platform_router import router as platform_router
from cappo_backend.api.routers.vnp_control_plane_router import router as vnp_admin_router
from cappo_backend.api.routers.vnp_router import router as vnp_router
from cappo_backend.api.routers.x402_router import api_x402_router, root_discovery_router
from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import SessionLocal
from cappo_backend.observability.logging import configure_logging
from cappo_backend.observability.middleware import RequestLoggingMiddleware
from cappo_backend.security.amphoteric_middleware import AmphotericSensingMiddleware
from cappo_backend.security.auth_middleware import AuthMiddleware
from cappo_backend.services.vnp_telemetry_service import VNPTelemetryService


async def vnp_prober_loop() -> None:
    """Optional local VNP prober loop; production uses external node telemetry."""
    while True:
        try:
            with SessionLocal() as db:
                import random

                from sqlalchemy import select

                from cappo_backend.models.vnp_models import APIState

                apis = db.execute(select(APIState)).scalars().all()
                telemetry_service = VNPTelemetryService(db)

                for api in apis:
                    regions = ["us-east", "us-west", "eu-west", "ap-southeast", "ap-northeast"]
                    for region in regions:
                        latency = random.randint(50, 800)
                        status = 200 if random.random() > 0.05 else 500
                        telemetry_service.ingest_probe(api.api_did, region, latency, status)

                db.commit()
        except Exception as e:
            print(f"VNP prober loop error: {e}")

        await asyncio.sleep(60) # Probe every minute in the backend


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_production()

    prober_task: asyncio.Task[None] | None = None
    if os.environ.get("ENABLE_VNP_PROBER") == "1" or os.getenv("CAPPO_ENABLE_INTERNAL_VNP_PROBER", "").lower() in {"1", "true", "yes"}:
        prober_task = asyncio.create_task(vnp_prober_loop())

    from cappo_backend.services.capi_registration import register_with_capi
    capi_task = asyncio.create_task(register_with_capi(settings))

    yield

    if capi_task:
        capi_task.cancel()

    if prober_task:
        prober_task.cancel()
        try:
            await prober_task
        except asyncio.CancelledError:
            pass


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Accepts explicit settings for testability."""
    settings = settings or get_settings()

    app = FastAPI(title="CAPPO Runtime", version="0.1.0", lifespan=lifespan)

    from cappo_backend.services.x402_payment import X402FreemiumASGI, get_x402_manager
    x402_manager = get_x402_manager(settings)
    if x402_manager.is_enabled:
        x402_routes = x402_manager.routes
        if settings.auth_enabled and settings.api_key_set:
            x402_routes = {
                route: config
                for route, config in x402_manager.routes.items()
                if route != "POST /v1/exec"
            }
        app.add_middleware(
            X402FreemiumASGI,
            server=x402_manager.server,
            routes=x402_routes,
            settings=settings,
        )

    # add_middleware adds outermost-last, so register innermost first.
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(AmphotericSensingMiddleware)
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

    app.include_router(vnp_router)
    app.include_router(vnp_admin_router)
    app.include_router(agents_router)
    app.include_router(exec_router)
    app.include_router(authorization_router)
    app.include_router(mcp_router)
    app.include_router(admin_router)
    app.include_router(audit_router)
    app.include_router(governance_v2_router)
    app.include_router(license_router)
    app.include_router(legacy_adapter_router)
    app.include_router(interlink_router, prefix="/api/interlink")
    app.include_router(interlink_vnp_router, prefix="/api/internal/interlink/vnp", tags=["interlink_vnp"])
    app.include_router(platform_router)
    app.include_router(benchmarks_router)

    app.include_router(gpc_router)
    app.include_router(api_x402_router, prefix="/api")
    app.include_router(root_discovery_router)
    from cappo_backend.api.routers.protocol import router as protocol_router
    from cappo_backend.api.routers.retrieval_governance_router import (
        router as retrieval_governance_router,
    )
    app.include_router(protocol_router)
    app.include_router(retrieval_governance_router)

    from fastapi import Request
    from fastapi.responses import JSONResponse
    
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        # Log the exception here in a real production system
        return JSONResponse(
            status_code=500,
            content={"error": "INTERNAL_SERVER_ERROR", "detail": "An unexpected error occurred. Stack trace is hidden for security."}
        )

    return app


app = create_app()
