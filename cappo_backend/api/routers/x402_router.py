from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cappo_backend.services.x402_payment import get_x402_manager

# Router for root-level discovery
root_discovery_router = APIRouter()

# Router for api/v1 level
api_x402_router = APIRouter(prefix="/v1/pricing", tags=["x402"])


@root_discovery_router.get("/.well-known/x402", summary="x402 Protocol Discovery")
@root_discovery_router.get("/.well-known/x402.json", summary="x402 Protocol Discovery (JSON)", include_in_schema=False)
async def get_well_known_x402() -> JSONResponse:
    """Return strict x402 discovery payload.

    As required by the marketplace, this must ONLY mention x402
    and not generic payment methods (Stripe, PayPal, etc.).
    """
    manager = get_x402_manager()
    # If disabled (e.g. during tests without a configured wallet), return fallback
    wallet = manager._config.evm_address if manager.is_enabled else "0x3a74772e925b54F7dAD7FD95c9Ba30825033f970"
    
    return JSONResponse(content={
        "name": "Sovereign AI Hub",
        "contact": "anthony@veklom.com",
        "wallet": wallet,
        "payment_methods": ["x402"],
        "pricing": "/api/v1/pricing",
        "x402": {
            "version": "2.0",
            "network": "eip155:8453",
            "currency": "USDC",
            "bazaar": "/x402/bazaar"
        }
    })


@api_x402_router.get("", summary="Pricing Manifest")
async def get_pricing_manifest() -> JSONResponse:
    """Return the pricing manifest mapping routes to x402 tiers.
    
    This ensures that /api/v1/pricing and the 402 challenges agree exactly.
    """
    manager = get_x402_manager()
    if not manager.is_enabled:
        return JSONResponse(content={"error": "x402 payment system disabled"})
    
    return JSONResponse(content=manager.pricing_manifest())
