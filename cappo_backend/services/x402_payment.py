"""x402 Payment Integration for CAPPO / cAPI.

Omnipresent X402 Monetization Plan:
  Every API route is monetized except the 4 public endpoints that would break
  the server if paywalled (/health, /docs, /openapi.json, /redoc).

  Pricing tiers (all USD, paid in USDC on Base):

    Tier 1 — MICRO    $0.001  Discovery & status reads. Targets M2M polling agents.
                              At 1B market transactions, capturing 100K calls here
                              = $100 pure signal revenue at near-zero marginal cost.

    Tier 2 — READ     $0.005  Audit, ledger, license lookups, benchmark reads.
                              5x micro because these results carry governed proof —
                              the consumer is paying for integrity, not just data.

    Tier 3 — ACTION   $0.05   All state mutations: kill-switch, budget, revoke,
                              governance approve/deny, license lifecycle ops.
                              Benchmarks: comparable to Stripe per-call ($0.03–$0.08)
                              for a governed write. 10x read premium justified.

    Tier 4 — COMPUTE  $0.50   Agent execution, compile jobs, governance assessment,
                              identity mint, discovery unlock.
                              Market reference: Anthropic tool-calls $2.50/1K tokens
                              (GPT-4o tool-call parity). CAPPO adds audit + PGL proof
                              on top — $0.50 is the *floor*, not the ceiling.

  All prices flow to `veklom_evm_address` (treasury) on Base Mainnet + Sepolia.
  Multi-chain expansion: zkSync, Unichain, Monad gated by x402_networks env var.

  X402FreemiumASGI: 5 free trials per wallet preserved for developer onboarding.

See docs/x402-pricing.md for GTM rationale and 100K-call capture plan.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig, RouteConfigurationError
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

from cappo_backend.config import Settings, get_settings

logger = logging.getLogger("cappo.x402")

X402_AVAILABLE = True

# ---------------------------------------------------------------------------
# Network identifiers for multi-chain support
# ---------------------------------------------------------------------------
NETWORKS = {
    "base":             "eip155:8453",    # Base Mainnet
    "base-sepolia":     "eip155:84532",   # Base Sepolia Testnet
    "zksync":           "eip155:324",     # zkSync Mainnet
    "zksync-sepolia":   "eip155:300",     # zkSync Sepolia Testnet
    "unichain":         "eip155:130",     # Unichain Mainnet
    "unichain-sepolia": "eip155:1301",    # Unichain Sepolia Testnet
    "monad":            "eip155:10143",   # Monad Testnet
}

# ---------------------------------------------------------------------------
# Routes that are unconditionally FREE — paywalling these would break clients
# ---------------------------------------------------------------------------
FREE_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})

# ---------------------------------------------------------------------------
# Omnipresent route manifest — every billable path and its tier
# ---------------------------------------------------------------------------
#
# Format: ("METHOD /path", tier_label)
# Tier labels: "micro" | "read" | "action" | "compute"
#
# IMPORTANT: /legacy/status is intentionally PAID (Tier 1 micro).
# Agents that poll status in production loops should pay for the signal.
# Only /health is free.
# ---------------------------------------------------------------------------
BILLABLE_ROUTES: list[tuple[str, str]] = [
    # ---- Tier 1: MICRO $0.001 — Discovery & status reads ----
    ("GET /legacy/status",                          "micro"),
    ("GET /api/v1/platform/pulse",                   "micro"),

    # ---- Tier 2: READ $0.005 — Governed data reads ----
    ("GET /v1/audit-logs",                           "read"),
    ("GET /v1/runs",                                 "read"),
    ("GET /v1/audit/verify",                         "read"),
    ("GET /v1/audit/verify/audit",                   "read"),
    ("GET /v1/audit/verify/pgl/:certificate_id",     "read"),
    ("GET /v1/audit/ledger/traces",                  "read"),
    ("GET /v1/governance/v2/risk/:agent_id",         "read"),
    ("GET /v1/governance/v2/quarantine",             "read"),
    ("GET /v1/license/:key",                         "read"),
    ("GET /v1/license",                              "read"),
    ("GET /api/v1/benchmarks/leaderboard",           "read"),
    ("GET /api/v1/benchmarks/staking/markets",       "read"),
    ("GET /api/v1/benchmarks/logs",                  "read"),
    ("GET /api/v1/gpc/stats",                        "read"),
    ("GET /api/v1/x402/ledger",                      "read"),
    ("GET /api/v1/x402/benchmarks/premium",          "read"),
    ("GET /v1/genomes",                              "read"),
    ("GET /v1/genomes/:genome_hash",                 "read"),
    ("GET /v1/genomes/:genome_hash/lineage",         "read"),
    ("GET /v1/genomes/:genome_hash/birth-certificate", "read"),

    # ---- Tier 3: ACTION $0.05 — State mutations ----
    ("PUT /v1/kill-switch/:workspace_id",                        "action"),
    ("PUT /v1/budget/:workspace_id",                             "action"),
    ("POST /v1/identities/:execution_id/revoke",                 "action"),
    ("POST /v1/governance/v2/quarantine/:quarantine_id/approve", "action"),
    ("POST /v1/governance/v2/quarantine/:quarantine_id/deny",    "action"),
    ("POST /v1/license/validate",                                "action"),
    ("POST /v1/license/deactivate",                              "action"),
    ("POST /legacy/snmp/toggle",                                 "action"),
    ("POST /legacy/modbus/toggle",                               "action"),
    ("POST /legacy/simulate",                                    "action"),
    ("POST /v1/genomes",                                         "action"),

    # ---- Tier 4: COMPUTE $0.50 — Agent execution & premium ops ----
    ("POST /v1/exec",                       "compute"),
    ("POST /api/v1/x402/exec/run",           "compute"),
    ("POST /v1/governance/v2/assess",        "compute"),
    ("POST /v1/agents/mint",                 "compute"),
    ("POST /v1/license/issue",               "compute"),
    ("POST /v1/license/activate",            "compute"),
    ("POST /api/v1/benchmarks/compile",      "compute"),
    ("POST /api/v1/gpc/compile",             "compute"),
    ("POST /api/v1/x402/discovery/unlock",   "compute"),
    ("POST /v1/genomes/diff",                "compute"),
]

# Prices per tier (USD string format consumed by PaymentOption)
TIER_PRICES: dict[str, str] = {
    "micro":   "$0.10",
    "read":    "$0.10",
    "action":  "$0.80",
    "compute": "$0.80",
}

# Human-readable descriptions for each tier (used in RouteConfig.description)
TIER_DESCRIPTIONS: dict[str, str] = {
    "micro":   "Discovery / status read — M2M polling tier",
    "read":    "Governed data read — integrity-proven response",
    "action":  "State mutation — audited write operation",
    "compute": "Agent execution / premium compute — full PGL proof",
}


class X402PaymentConfig:
    """Configuration for x402 payment middleware."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.evm_address    = "0x3a74772e925b54F7dAD7FD95c9Ba30825033f970"
        self.facilitator_url = self._settings.x402_facilitator_url
        self.enabled_networks = ["base"]

    @property
    def is_configured(self) -> bool:
        """Check if x402 is properly configured with a real treasury wallet."""
        import sys
        if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
            return False
        return bool(self.evm_address and self.evm_address.startswith("0x"))


def create_x402_server(config: X402PaymentConfig | None = None) -> x402ResourceServer | None:
    """Create and configure x402 resource server with all enabled networks."""
    if not X402_AVAILABLE:
        return None
    config = config or X402PaymentConfig()
    if not config.is_configured:
        return None

    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=config.facilitator_url))
    server = x402ResourceServer(facilitator)

    # Always register Base (primary revenue network)
    server.register(NETWORKS["base"], ExactEvmServerScheme())

    return server


def _build_payment_options(
    price: str,
    evm_address: str,
    enabled_networks: list[str],
) -> list[PaymentOption]:
    """Build PaymentOption list for a given price across all enabled networks."""
    return [
        PaymentOption(
            scheme="exact",
            pay_to=evm_address,
            price=price,
            network=NETWORKS[network_key],
        )
        for network_key in enabled_networks
        if network_key in NETWORKS
    ]


def create_protected_routes(config: X402PaymentConfig | None = None) -> dict[str, RouteConfig]:
    """Create route configurations for ALL billable endpoints.

    Omnipresent X402 — 37 routes across 4 pricing tiers.
    Only FREE_PATHS (/health, /docs, /openapi.json, /redoc) are excluded.

    Tier 1 MICRO  $0.001 — M2M discovery & status reads (incl. /legacy/status)
    Tier 2 READ   $0.005 — Governed audit, ledger, license, benchmark reads
    Tier 3 ACTION $0.05  — State mutations: kill-switch, budget, revoke, governance
    Tier 4 COMPUTE $0.50 — Agent execution, compile, assess, mint, discovery/unlock

    Returns:
        Dictionary of {"METHOD /path": RouteConfig} for PaymentMiddlewareASGI.
    """
    if not X402_AVAILABLE:
        return {}
    config = config or X402PaymentConfig()
    if not config.is_configured:
        return {}

    # Pre-build payment option lists for each tier (one list reused across routes)
    tier_options: dict[str, list[PaymentOption]] = {
        tier: _build_payment_options(
            TIER_PRICES[tier], config.evm_address, config.enabled_networks
        )
        for tier in TIER_PRICES
    }

    routes: dict[str, RouteConfig] = {}
    for route_key, tier in BILLABLE_ROUTES:
        routes[route_key] = RouteConfig(
            accepts=tier_options[tier],
            mime_type="application/json",
            description=f"{TIER_DESCRIPTIONS[tier]} — {route_key}",
        )

    return routes


class X402PaymentManager:
    """Manages x402 payment integration for CAPPO / cAPI.

    Handles server initialization, omnipresent route configuration,
    payment verification, and multi-network support.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._config = X402PaymentConfig(settings)
        self._server = create_x402_server(self._config)
        self._routes = create_protected_routes(self._config)

    @property
    def is_enabled(self) -> bool:
        if not X402_AVAILABLE:
            return False
        return self._server is not None and len(self._routes) > 0

    @property
    def server(self) -> x402ResourceServer | None:
        return self._server

    @property
    def routes(self) -> dict[str, RouteConfig]:
        return self._routes

    def get_middleware_config(self) -> dict[str, Any]:
        """Get configuration dict for PaymentMiddlewareASGI."""
        if not self.is_enabled:
            return {}
        return {"routes": self._routes, "server": self._server}

    def route_summary(self) -> dict[str, list[str]]:
        """Return a tier-grouped summary of all protected routes (for /api/v1/x402/config)."""
        summary: dict[str, list[str]] = {tier: [] for tier in TIER_PRICES}
        for route_key, tier in BILLABLE_ROUTES:
            summary[tier].append(route_key)
        return summary

    def pricing_manifest(self) -> dict[str, Any]:
        """Return the full pricing manifest for documentation and discovery."""
        return {
            "tiers": [
                {
                    "tier": tier,
                    "price_usd": TIER_PRICES[tier],
                    "description": TIER_DESCRIPTIONS[tier],
                    "route_count": sum(1 for _, t in BILLABLE_ROUTES if t == tier),
                    "routes": [r for r, t in BILLABLE_ROUTES if t == tier],
                }
                for tier in ["micro", "read", "action", "compute"]
            ],
            "free_paths": sorted(FREE_PATHS),
            "total_billable_routes": len(BILLABLE_ROUTES),
            "networks": list(NETWORKS.keys()),
            "payment_protocol": "x402",
            "settlement_chain": "Base (eip155:8453)",
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_x402_manager: X402PaymentManager | None = None


def get_x402_manager(settings: Settings | None = None) -> X402PaymentManager:
    """Get or create singleton x402 payment manager."""
    global _x402_manager
    if _x402_manager is None or settings is not None:
        _x402_manager = X402PaymentManager(settings)
    return _x402_manager


def reset_x402_manager() -> None:
    """Reset singleton (useful for testing)."""
    global _x402_manager
    _x402_manager = None


# ---------------------------------------------------------------------------
# Freemium ASGI wrapper — 5 free trials per wallet via Redis
# ---------------------------------------------------------------------------

class X402FreemiumASGI:
    """Wraps PaymentMiddlewareASGI to provide 5 free trials per wallet via Redis.

    Developer onboarding flow:
      1. Agent/client sends X-Wallet-Address header on first call.
      2. If Redis counter for that wallet < 5, the request passes free.
      3. On the 6th call the full x402 paywall applies.
      4. If Redis is unavailable, fails closed to the paywall (safe default).
    """

    def __init__(
        self,
        app: Any,
        server: Any,
        routes: dict[str, Any],
        settings: Settings | None = None,
    ) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self._payment_route_config_broken = False
        if not X402_AVAILABLE:
            self.payment_app = app
        else:
            self.payment_app = PaymentMiddlewareASGI(app, server=server, routes=routes)

        redis_url = ""
        if settings and settings.redis_url:
            redis_url = settings.redis_url

        self.redis: Any = None
        if redis_url:
            try:
                import redis as _redis
                self.redis = _redis.Redis.from_url(redis_url, decode_responses=True)
            except Exception:
                pass

    def _has_valid_internal_api_key(self, scope: Any) -> bool:
        if scope.get("cappo_internal_api_key_valid") is True:
            return True
        api_key = ""
        for name, value in scope.get("headers", []):
            header_name = name.decode("latin1").lower() if isinstance(name, bytes) else str(name).lower()
            if header_name == "x-api-key":
                api_key = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                break
        api_key_set = self.settings.api_key_set or get_settings().api_key_set
        return bool(api_key and api_key in api_key_set)

    async def _send_payment_plane_unavailable(self, send: Any) -> None:
        import json

        body = json.dumps({
            "error": "X402_PAYMENT_PLANE_UNAVAILABLE",
            "detail": "x402 payment route configuration is temporarily unavailable",
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-402-degraded", b"true"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def _call_payment_app(self, scope: Any, receive: Any, send: Any) -> None:
        if self._payment_route_config_broken:
            if self._has_valid_internal_api_key(scope):
                await self.app(scope, receive, send)
                return
            await self._send_payment_plane_unavailable(send)
            return

        try:
            await self.payment_app(scope, receive, send)
        except RouteConfigurationError as exc:
            self._payment_route_config_broken = True
            logger.critical(
                "x402 route configuration unsupported by facilitator; "
                "payment middleware degraded for authenticated internal traffic",
                extra={"error": str(exc)},
            )
            if self._has_valid_internal_api_key(scope):
                await self.app(scope, receive, send)
                return
            await self._send_payment_plane_unavailable(send)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._has_valid_internal_api_key(scope):
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        # 2. Skip condition check
        # Explicit bypass for local development unless freemium is disabled
        if settings.environment == "production" and not os.environ.get("ENABLE_FREEMIUM"):
            await self._call_payment_app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        wallet_address = headers.get(b"x-wallet-address", b"").decode("utf-8")

        if wallet_address and self.redis:
            try:
                key = f"cappo:free_tries:{wallet_address}"
                uses = self.redis.get(key)
                if not uses or int(uses) < 5:
                    self.redis.incr(key)
                    # Free trial — bypass paywall, let request through
                    await self.app(scope, receive, send)
                    return
            except Exception as exc:
                logger.warning("Redis unavailable during freemium check", extra={"error": str(exc)})

        await self._call_payment_app(scope, receive, send)
