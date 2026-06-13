"""x402 Payment Integration for CAPPO.

Enables instant stablecoin payments for agent execution via HTTP 402.
Integrates with Coinbase's x402 protocol for programmatic commerce.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
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
        
        # Wallet addresses from environment or settings
        self.evm_address = os.getenv(
            "VEKLOM_EVM_ADDRESS", 
            self._settings.veklom_evm_address or "0x3a74772e925b54F7dAD7FD95c9Ba30825033f970"
        )
        
        # Load x402 configuration
        self.facilitator_url = os.getenv(
            "X402_FACILITATOR_URL",
            "https://x402.org/facilitator"
        )
        
        # Pricing configuration (in USD)
        self.exec_price = os.getenv("X402_EXEC_PRICE", "$0.001")
        self.mint_price = os.getenv("X402_MINT_PRICE", "$0.005")
        
        # Supported networks (comma-separated)
        self.enabled_networks = os.getenv(
            "X402_NETWORKS",
            "base,base-sepolia,zksync,unichain,monad"
        ).split(",")
    
    @property
    def is_configured(self) -> bool:
        """Check if x402 is properly configured with wallet address."""
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
    
    Defines pricing for:
    - POST /v1/exec - Agent execution ($0.001)
    - POST /v1/agents/mint - Agent certificate minting ($0.005)
    
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
    
    # Build payment options for each enabled network
    payment_options = []
    
    for network_key in config.enabled_networks:
        network_key = network_key.strip()
        if network_key in NETWORKS:
            payment_options.append(
                PaymentOption(
                    scheme="exact",
                    pay_to=evm_address,
                    price=config.exec_price,
                    network=NETWORKS[network_key],
                )
            )
    
    routes = {
        # Agent execution endpoint
        "POST /v1/exec": RouteConfig(
            accepts=payment_options,
            mime_type="application/json",
            description="Execute governed agent with PGL validation",
        ),
        
        # Agent minting endpoint (higher price for certificate creation)
        "POST /v1/agents/mint": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=evm_address,
                    price=config.mint_price,
                    network=NETWORKS[network_key],
                )
                for network_key in config.enabled_networks
                if network_key in NETWORKS
            ],
            mime_type="application/json",
            description="Mint new agent certificate on veklom registry",
        ),
        
        # Ledger query (lower price for read operations)
        "GET /v1/ledger/:agent_id": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=evm_address,
                    price="$0.0001",  # Cheaper for reads
                    network=NETWORKS[network_key],
                )
                for network_key in config.enabled_networks
                if network_key in NETWORKS
            ],
            mime_type="application/json",
            description="Query agent ledger history",
        ),
    }
    
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


def get_x402_manager() -> X402PaymentManager:
    """Get or create singleton x402 payment manager."""
    global _x402_manager
    if _x402_manager is None:
        _x402_manager = X402PaymentManager()
    return _x402_manager


def reset_x402_manager() -> None:
    """Reset singleton (useful for testing)."""
    global _x402_manager
    _x402_manager = None
