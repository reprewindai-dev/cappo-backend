"""CAPPO runtime FastAPI application.

Middleware stack order (migration note §7):
    1. CORS (outermost - handles preflight)
    2. x402 Payment (HTTP 402 Payment Required for agent execution)
    3. Auth / entitlement  (TODO: wire)
    4. Budget / kill-switch  (TODO: wire — 402 takes precedence)
    5. EI enforcement is handled *inside* the governed pipeline route

There is **no** public-path bypass for ``/v1/exec`` — every request goes through
the governed orchestrator (Option A).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

from cappo_backend.api.routers.admin_router import router as admin_router
from cappo_backend.api.routers.exec_router import router as exec_router
from cappo_backend.api.routers.genome_router import router as genome_router
from cappo_backend.api.routers.license_router import router as license_router
from cappo_backend.config import get_settings
from cappo_backend.security.logging import setup_logging
from cappo_backend.security.middleware import (
    AuthEntitlementMiddleware,
    EATEnforcementMiddleware,
    PaymentGateMiddleware,
)
from cappo_backend.services.x402_payment import get_x402_manager

# Initialize logging configuration based on settings
settings = get_settings()
setup_logging(settings)

app = FastAPI(title="CAPPO Runtime", version="0.1.0")

# Initialize x402 payment manager
x402_manager = get_x402_manager()

# Register middlewares in correct precedence order.
# Starlette wraps middlewares inside out: the middleware added LAST runs FIRST.
# Preflight OPTIONS requests must bypass auth, so CORSMiddleware runs first.

# Add x402 payment middleware if enabled
if x402_manager.is_enabled and settings.x402_enabled:
    try:
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        x402_config = x402_manager.get_middleware_config()
        app.add_middleware(
            PaymentMiddlewareASGI,
            routes=x402_config["routes"],
            server=x402_config["server"],
        )
        print(f"[OK] x402 payment middleware enabled")
        print(f"  - EVM Address: {settings.veklom_evm_address}")
        print(f"  - Networks: {settings.x402_networks}")
        print(f"  - Exec price: {settings.x402_exec_price}")
    except ImportError:
        print("[WARN] x402 package not installed. Run: pip install x402")
    except Exception as e:
        print(f"[WARN] x402 middleware error: {e}")

app.add_middleware(PaymentGateMiddleware)
app.add_middleware(EATEnforcementMiddleware)
app.add_middleware(AuthEntitlementMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Edge Gateway for EAT verification (stored on app.state)
try:
    from cappo_backend.security.edge_gateway import EdgeGateway
    from cappo_backend.security.nonce_cache import InMemoryNonceCache
    from cappo_backend.services.audit_service import AuditService
    from cappo_backend.db.session import SessionLocal

    _edge_db = SessionLocal()
    _edge_audit = AuditService(_edge_db)
    _nonce_cache = InMemoryNonceCache()
    app.state.edge_gateway = EdgeGateway(
        audit=_edge_audit,
        eat_signing_key=settings.eat_signing_key,
        nonce_cache=_nonce_cache,
        audience=settings.edge_mcp_identity,
    )
    print(f"[OK] Edge Gateway initialized (audience={settings.edge_mcp_identity})")
except Exception as e:
    print(f"[WARN] Edge Gateway not initialized: {e}")
    app.state.edge_gateway = None

app.include_router(exec_router)
app.include_router(admin_router)
app.include_router(genome_router)
app.include_router(license_router)


@app.get("/health")
def healthcheck() -> dict[str, Any]:
    """Health check with x402 and veklom status."""
    status = {
        "status": "ok",
        "version": "0.1.0",
        "services": {
            "cappo": "healthy",
            "x402_payments": "enabled" if x402_manager.is_enabled else "disabled",
            "veklom_pgl": "connected" if settings.veklom_byos_backend_url else "local_mode",
            "edge_gateway": "active" if getattr(app.state, "edge_gateway", None) else "disabled",
            "eat_system": "enabled" if settings.eat_signing_key else "disabled",
        },
        "config": {
            "environment": settings.environment,
            "ei_signing_provider": settings.ei_signing_provider,
            "eat_signing_provider": settings.eat_signing_provider,
            "eat_ttl_seconds": settings.eat_default_ttl_seconds,
            "x402_enabled": settings.x402_enabled,
            "x402_networks": settings.x402_networks if settings.x402_enabled else None,
            "veklom_evm_address": settings.veklom_evm_address[:10] + "..." if settings.veklom_evm_address else None,
        },
    }
    return status


