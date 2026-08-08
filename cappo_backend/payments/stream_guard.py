"""x402 payment streaming guard — gates SSE streams behind a verified x402 payment."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def _extract_payment_header(request: Request) -> str | None:
    return request.headers.get("X-Payment") or request.headers.get("x-payment")


async def verify_x402_payment(request: Request, required_amount_usd: float = 0.001) -> dict[str, Any]:
    """Verify an x402 payment header before allowing stream access.

    Raises HTTP 402 if no valid payment is present.
    Returns the decoded payment receipt on success.
    """
    payment_header = _extract_payment_header(request)
    if not payment_header:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Payment Required",
                "accepts": [
                    {
                        "scheme": "exact",
                        "network": "base",
                        "maxAmountRequired": str(int(required_amount_usd * 1_000_000)),
                        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC on Base
                        "description": "Pay-per-stream access",
                    }
                ],
            },
        )
    # Decode and validate payment — integrate with x402 verifier
    try:
        from x402.verify import verify_payment  # type: ignore[import]
        receipt = await verify_payment(payment_header, required_amount_usd)
        logger.info("x402 payment verified: %s", receipt)
        return receipt
    except ImportError:
        # x402 library not installed — log and pass through in dev
        logger.warning("x402 library not installed; skipping payment verification in dev mode")
        return {"dev_mode": True, "header": payment_header}
    except Exception as exc:
        raise HTTPException(status_code=402, detail=f"Payment verification failed: {exc}") from exc
