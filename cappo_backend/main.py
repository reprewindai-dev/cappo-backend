"""CAPPO runtime FastAPI application.

Middleware stack order (migration note §7):
    1. Auth / entitlement  (TODO: wire)
    2. Budget / kill-switch  (TODO: wire — 402 takes precedence)
    3. EI enforcement is handled *inside* the governed pipeline route

There is **no** public-path bypass for ``/v1/exec`` — every request goes through
the governed orchestrator (Option A).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cappo_backend.api.routers.admin_router import router as admin_router
from cappo_backend.api.routers.audit_router import router as audit_router
from cappo_backend.api.routers.exec_router import router as exec_router
from cappo_backend.config import get_settings
from cappo_backend.observability.logging import configure_logging
from cappo_backend.observability.middleware import RequestLoggingMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Structured JSON logging for the whole process (GAP: observability).
    configure_logging(settings.log_level)
    # Fail-closed: a production deployment refuses to start with insecure
    # defaults (insecure EI key, non-persistent PGL, SQLite). No-op in dev/test.
    settings.validate_production()
    yield


app = FastAPI(title="CAPPO Runtime", version="0.1.0", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.include_router(exec_router)
app.include_router(admin_router)
app.include_router(audit_router)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
