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
from cappo_backend.api.routers.exec_router import router as exec_router
from cappo_backend.config import Settings, get_settings
from cappo_backend.observability.logging import configure_logging
from cappo_backend.observability.middleware import RequestLoggingMiddleware
from cappo_backend.security.auth_middleware import AuthMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Structured JSON logging for the whole process (GAP: observability).
    configure_logging(settings.log_level)
    # Fail-closed: a production deployment refuses to start with insecure
    # defaults (insecure EI key, non-persistent PGL, SQLite, auth off). No-op in dev/test.
    settings.validate_production()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Accepts explicit settings for testability."""
    settings = settings or get_settings()

    app = FastAPI(title="CAPPO Runtime", version="0.1.0", lifespan=lifespan)

    # add_middleware adds outermost-last, so register innermost first.
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    app.include_router(exec_router)
    app.include_router(admin_router)
    app.include_router(audit_router)

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
