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

from cappo_backend.api.routers.admin_router import router as admin_router
from cappo_backend.api.routers.audit_router import router as audit_router
from cappo_backend.api.routers.exec_router import router as exec_router
from cappo_backend.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Fail-closed: a production deployment refuses to start with insecure
    # defaults (insecure EI key, non-persistent PGL, SQLite). No-op in dev/test.
    get_settings().validate_production()
    yield


app = FastAPI(title="CAPPO Runtime", version="0.1.0", lifespan=lifespan)

app.include_router(exec_router)
app.include_router(admin_router)
app.include_router(audit_router)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
