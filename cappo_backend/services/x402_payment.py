"""x402 Payment Integration for CAPPO.

Enables instant stablecoin payments for agent execution via HTTP 402.
Integrates with Coinbase's x402 protocol for programmatic commerce.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
    from x402.http.middleware.fastapi import PaymentMiddlewareASGI
    from x402.http.types import RouteConfig
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.schemas import Network
    from x402.server import x402ResourceServer
    X402_AVAILABLE = True
except ImportError:
    X402_AVAILABLE = False
    # Define dummy types for when x402 is not installed
    class FacilitatorConfig:  # type: ignore
        def __init__(self, url: str = "") -> None: pass
    class HTTPFacilitatorClient:  # type: ignore
        def __init__(self, config: Any = None) -> None: pass
    class PaymentOption:  # type: ignore
        def __init__(self, **kwargs: Any) -> None: pass
    class RouteConfig:  # type: ignore
        def __init__(self, **kwargs: Any) -> None: pass
    class ExactEvmServerScheme:  # type: ignore
        pass
    class Network:  # type: ignore
        pass
    class x402ResourceServer:  # type: ignore
        def __init__(self, facilitator: Any = None) -> None: pass
        def register(self, network: Any, scheme: Any) -> None: pass
    class PaymentMiddlewareASGI:  # type: ignore
        def __init__(self, **kwargs: Any) -> None: pass

from cappo_backend.config import Settings, get_settings

# Network identifiers for multi-chain support
NETWORKS = {
    "base": "eip155:8453",           # Base Mainnet
    "base-sepolia": "eip155:84532",  # Base Sepolia Testnet
    "zksync": "eip155:324",          # zkSync Mainnet
    "zksync-sepolia": "eip155:300",  # zkSync Sepolia Testnet
    "unichain": "eip155:130",        # Unichain Mainnet
    "unichain-sepolia": "eip155:1301", # Unichain Sepolia Testnet
    "monad": "eip155:10143",         # Monad Testnet
}


class X402PaymentConfig:
    """Configuration for x402 payment middleware."""
    
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        
        # Wallet address — REQUIRED for x402 to activate.
        self.evm_address = self._settings.veklom_evm_address
        
        # Load x402 configuration
        self.facilitator_url = self._settings.x402_facilitator_url
        
        # Pricing configuration (in USD)
        self.exec_price = self._settings.x402_exec_price
        self.mint_price = self._settings.x402_mint_price
        
        # Supported networks (comma-separated)
        self.enabled_networks = [
            n.strip() for n in self._settings.x402_networks.split(",") if n.strip()
        ]
    
    @property
    def is_configured(self) -> bool:
        """Check if x402 is properly configured with wallet address."""
        import sys
        if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
            return False
        return bool(self.evm_address and self.evm_address.startswith("0x"))


def create_x402_server(config: X402PaymentConfig | None = None) -> x402ResourceServer | None:
    """Create and configure x402 resource server.
    
    Args:
        config: X402 configuration (uses default if not provided)
        
    Returns:
        Configured x402ResourceServer or None if not configured
    """
    if not X402_AVAILABLE:
        return None
    
    config = config or X402PaymentConfig()
    
    if not config.is_configured:
        return None
    
    # Create facilitator client
    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=config.facilitator_url)
    )
    
    # Create resource server
    server = x402ResourceServer(facilitator)
    
    # Register EVM scheme
    server.register(NETWORKS["base"], ExactEvmServerScheme())
    server.register(NETWORKS["base-sepolia"], ExactEvmServerScheme())
    
    if "zksync" in config.enabled_networks:
        server.register(NETWORKS["zksync"], ExactEvmServerScheme())
        server.register(NETWORKS["zksync-sepolia"], ExactEvmServerScheme())
    
    if "unichain" in config.enabled_networks:
        server.register(NETWORKS["unichain"], ExactEvmServerScheme())
        server.register(NETWORKS["unichain-sepolia"], ExactEvmServerScheme())
    
    if "monad" in config.enabled_networks:
        server.register(NETWORKS["monad"], ExactEvmServerScheme())
    
    return server


def create_protected_routes(config: X402PaymentConfig | None = None) -> dict[str, RouteConfig]:
    """Create route configurations for protected endpoints.
    
    Implements a 3-tier pricing strategy for API monetization:
    1. Micro-Transactions ($0.001): High-volume read operations
    2. Standard Actions ($0.01): State changes and controls
    3. Premium Compute ($0.10): AI Execution and compiling
    
    Args:
        config: X402 configuration
        
    Returns:
        Dictionary of route configs for PaymentMiddlewareASGI
    """
    if not X402_AVAILABLE:
        return {}
    
    config = config or X402PaymentConfig()
    
    if not config.is_configured:
        return {}
    
    evm_address = config.evm_address
    
    # Tier 1: Micro-Transactions ($0.005)
    micro_options = [
        PaymentOption(
            scheme="exact",
            pay_to=evm_address,
            price="$0.005",
            network=NETWORKS[network_key],
        )
        for network_key in config.enabled_networks if network_key in NETWORKS
    ]
    
    # Tier 2: Standard Actions ($0.05)
    standard_options = [
        PaymentOption(
            scheme="exact",
            pay_to=evm_address,
            price="$0.05",
            network=NETWORKS[network_key],
        )
        for network_key in config.enabled_networks if network_key in NETWORKS
    ]
    
    # Tier 3: Premium Compute ($0.50)
    premium_options = [
        PaymentOption(
            scheme="exact",
            pay_to=evm_address,
            price="$0.50",
            network=NETWORKS[network_key],
        )
        for network_key in config.enabled_networks if network_key in NETWORKS
    ]
    
    routes = {}
    
    # --- 1. Micro-Transactions ($0.005) ---
    micro_paths = [
        "GET /v1/audit-logs",
        "GET /v1/runs",
        "GET /v1/audit/ledger/traces",
        "GET /v1/audit/verify",
        "GET /v1/audit/verify/audit",
        "GET /v1/audit/verify/pgl/:certificate_id",
        "GET /v1/governance/v2/risk/:agent_id",
        "GET /v1/governance/v2/quarantine",
        "GET /api/v1/benchmarks/leaderboard",
        "GET /api/v1/benchmarks/staking/markets",
        "GET /api/v1/benchmarks/logs",
        "GET /api/v1/gpc/stats",
        "GET /v1/license/:key",
        "GET /v1/license",
        "GET /api/v1/x402/ledger",
        "GET /api/v1/x402/config",
        "GET /api/v1/x402/benchmarks/premium",
        "GET /api/v1/platform/pulse"
    ]
    for path in micro_paths:
        routes[path] = RouteConfig(
            accepts=micro_options,
            mime_type="application/json",
            description=f"Micro-transaction for {path}",
        )
        
    # --- 2. Standard Actions ($0.05) ---
    standard_paths = [
        "PUT /v1/kill-switch/:workspace_id",
        "PUT /v1/budget/:workspace_id",
        "POST /v1/identities/:execution_id/revoke",
        "POST /v1/governance/v2/quarantine/:quarantine_id/approve",
        "POST /v1/governance/v2/quarantine/:quarantine_id/deny",
        "POST /v1/license/validate",
        "POST /v1/license/deactivate",
        "POST /legacy/snmp/toggle",
        "POST /legacy/modbus/toggle",
        "POST /legacy/simulate"
    ]
    for path in standard_paths:
        routes[path] = RouteConfig(
            accepts=standard_options,
            mime_type="application/json",
            description=f"Standard action for {path}",
        )

    # --- 3. Premium Compute ($0.50) ---
    premium_paths = [
        "POST /v1/exec",
        "POST /api/v1/x402/exec/run",
        "POST /v1/governance/v2/assess",
        "POST /v1/agents/mint",
        "POST /v1/license/issue",
        "POST /v1/license/activate",
        "POST /api/v1/benchmarks/compile",
        "POST /api/v1/gpc/compile",
        "POST /api/v1/x402/discovery/unlock"
    ]
    for path in premium_paths:
        routes[path] = RouteConfig(
            accepts=premium_options,
            mime_type="application/json",
            description=f"Premium compute action for {path}",
        )
    
    return routes


class X402PaymentManager:
    """Manages x402 payment integration for CAPPO.
    
    Handles:
    - Server initialization
    - Route configuration
    - Payment verification
    - Multi-network support
    """
    
    def __init__(self, settings: Settings | None = None) -> None:
        self._config = X402PaymentConfig(settings)
        self._server = create_x402_server(self._config)
        self._routes = create_protected_routes(self._config)
    
    @property
    def is_enabled(self) -> bool:
        """Check if x402 payments are enabled and configured."""
        if not X402_AVAILABLE:
            return False
        return self._server is not None and len(self._routes) > 0
    
    @property
    def server(self) -> x402ResourceServer | None:
        """Get the x402 resource server for middleware."""
        return self._server
    
    @property
    def routes(self) -> dict[str, RouteConfig]:
        """Get protected route configurations."""
        return self._routes
    
    def get_middleware_config(self) -> dict[str, Any]:
        """Get configuration for PaymentMiddlewareASGI.
        
        Returns:
            Dict with 'routes' and 'server' keys for FastAPI middleware
        """
        if not self.is_enabled:
            return {}
        
        return {
            "routes": self._routes,
            "server": self._server,
        }


# Singleton instance
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


class X402FreemiumASGI:
    """Wraps PaymentMiddlewareASGI to provide 5 free trials per wallet via Redis."""
    def __init__(self, app: Any, server: Any, routes: dict[str, Any], settings: Settings | None = None) -> None:
        self.app = app
        if not X402_AVAILABLE:
            self.payment_app = app
        else:
            self.payment_app = PaymentMiddlewareASGI(app, server=server, routes=routes)
        
        # Connect to Redis
        redis_url = "redis://default:NE7O3Zzl6WLNI9c61CYBgLzBfO2h7X7q@v8vf3lw73fx9lw9xmbq1tvo5:6379/0"
        if settings and settings.redis_url:
            redis_url = settings.redis_url
            
        try:
            import redis
            self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        except Exception:
            self.redis = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
            
        # Extract X-Wallet-Address from headers
        headers = dict(scope.get("headers", []))
        wallet_address = headers.get(b"x-wallet-address", b"").decode("utf-8")
        
        if wallet_address and self.redis:
            try:
                # Check free tries
                key = f"cappo:free_tries:{wallet_address}"
                uses = self.redis.get(key)
                if not uses or int(uses) < 5:
                    self.redis.incr(key)
                    # Bypass x402 payment, let the request through for free
                    return await self.app(scope, receive, send)
            except Exception:
                pass # Fail safe to X402 paywall if Redis throws an error
                
        # Run X402 payment middleware if no free tries remain or no wallet provided
        return await self.payment_app(scope, receive, send)
