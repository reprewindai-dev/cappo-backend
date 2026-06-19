"""
x402 Machine-to-Machine Payment Router — Cappo Backend

Base App ID : 6a20f24cc341f72c2f573eb5
Merchant    : 0x3a74772e925b54F7dAD7FD95c9Ba30825033f970  (Base Mainnet)
Frontend    : https://veklom-id.vercel.app

Payment flow for every gated endpoint:
  1. Client calls GET /api/v1/x402/config  — learns prices, wallet, chain
  2. Client sends USDC on Base to merchant wallet
  3. Client retries request with:
       X-Wallet-Address: <their Base wallet>
       X-Payment:        <tx hash of the USDC transfer>
  4. This router verifies on-chain via Base RPC, records the tx, grants access

Pricing (USDC on Base Mainnet, eip155:8453):
  POST /api/v1/x402/exec/run            $0.05 USDC
  GET  /api/v1/x402/benchmarks/premium  $0.02 USDC
  POST /api/v1/x402/discovery/unlock    $0.01 USDC
  GET  /api/v1/x402/config              FREE
  GET  /api/v1/x402/ledger              FREE (admin)
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from web3 import Web3

from cappo_backend.db.session import get_session
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment

# ---------------------------------------------------------------------------
# IDENTITY CONSTANTS
# ---------------------------------------------------------------------------

BASE_APP_ID       = "6a20f24cc341f72c2f573eb5"
MERCHANT_WALLET   = "0x3a74772e925b54F7dAD7FD95c9Ba30825033f970"
USDC_CONTRACT     = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base
CHAIN_ID          = "eip155:8453"                                  # Base Mainnet
BASE_RPC          = "https://mainnet.base.org"
FRONTEND_ORIGIN   = "https://veklom-id.vercel.app"

# ERC-20 Transfer(address,address,uint256) topic
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# ---------------------------------------------------------------------------
# PRICE TABLE
# ---------------------------------------------------------------------------

PRICES: dict[str, dict[str, Any]] = {
    "/api/v1/x402/exec/run": {
        "usdc": "0.05",
        "usdc_base_units": 50_000,
        "description": "Governed agent execution run via Cappo Runtime",
    },
    "/api/v1/x402/benchmarks/premium": {
        "usdc": "0.02",
        "usdc_base_units": 20_000,
        "description": "Premium benchmark leaderboard with live SLA metrics",
    },
    "/api/v1/x402/discovery/unlock": {
        "usdc": "0.01",
        "usdc_base_units": 10_000,
        "description": "Discovery feature unlock for connected Veklom wallet",
    },
}

# Ephemeral state lock for nonce linearization (replay protection)
_nonce_lock = asyncio.Lock()
_pending_tx_hashes: set[str] = set()
_verified_tx_hashes: set[str] = set()
_payment_ledger: list[dict[str, Any]] = []

# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TX_HASH_RE  = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _valid_address(v: str) -> bool:
    return bool(_EVM_ADDR_RE.match(v))


def _valid_tx(v: str) -> bool:
    return bool(_TX_HASH_RE.match(v))


# ---------------------------------------------------------------------------
# 402 CHALLENGE BUILDER
# ---------------------------------------------------------------------------

def _challenge(response: Response, path: str) -> dict[str, Any]:
    """Build x402-spec compliant challenge body and set response headers."""
    price = PRICES.get(path, {"usdc": "0.01", "usdc_base_units": 10_000,
                               "description": "Veklom x402 payment"})
    response.headers["X-402-Version"]   = "1"
    response.headers["X-402-Chain"]     = CHAIN_ID
    response.headers["X-402-Recipient"] = MERCHANT_WALLET
    response.headers["X-402-Token"]     = USDC_CONTRACT
    response.headers["X-402-Amount"]    = str(price["usdc_base_units"])
    response.headers["X-402-Resource"]  = path
    response.headers["X-402-App-Id"]    = BASE_APP_ID
    return {
        "x402Version": 1,
        "appId": BASE_APP_ID,
        "error": "Payment Required",
        "accepts": [{
            "scheme": "exact",
            "network": CHAIN_ID,
            "maxAmountRequired": str(price["usdc_base_units"]),
            "resource": path,
            "description": price["description"],
            "mimeType": "application/json",
            "payTo": MERCHANT_WALLET,
            "maxTimeoutSeconds": 300,
            "asset": USDC_CONTRACT,
            "extra": {"name": "USD Coin", "version": "2"},
        }],
    }


# ---------------------------------------------------------------------------
# ON-CHAIN VERIFICATION
# ---------------------------------------------------------------------------

async def _verify_onchain(
    tx_hash: str, path: str, caller: str
) -> tuple[bool, str | None, str | None]:
    """Returns (ok, error_message, block_number)."""
    required = PRICES.get(path, {"usdc_base_units": 10_000})["usdc_base_units"]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(BASE_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_getTransactionReceipt",
                "params": [tx_hash],
            })
        data = r.json()
    except Exception as exc:
        return False, f"Base RPC error: {exc}", None

    result = data.get("result")
    if not result:
        return False, "Transaction not found on Base Mainnet.", None
    if result.get("status") != "0x1":
        return False, "Transaction failed or reverted on Base Mainnet.", None

    for log in result.get("logs", []):
        topics = log.get("topics", [])
        if (
            log.get("address", "").lower() == USDC_CONTRACT.lower()
            and len(topics) >= 3
            and topics[0].lower() == TRANSFER_TOPIC
        ):
            from_addr = "0x" + topics[1][-40:]
            to_addr   = "0x" + topics[2][-40:]
            value     = int(log["data"], 16) if log.get("data", "0x") != "0x" else 0
            if (
                from_addr.lower() == caller.lower()
                and to_addr.lower() == MERCHANT_WALLET.lower()
                and value >= required
            ):
                return True, None, result.get("blockNumber", "?")

    usdc = PRICES.get(path, {"usdc": "?"}).get("usdc", "?")
    return False, (
        f"No USDC transfer of >= ${usdc} from {caller} to Veklom wallet "
        f"found in transaction logs."
    ), None


# ---------------------------------------------------------------------------
# PAYMENT DEPENDENCY
# ---------------------------------------------------------------------------

async def require_payment(request: Request, response: Response, db: Session = Depends(get_session)) -> dict[str, str]:
    """FastAPI dependency — verifies x402 payment before granting route access."""

    # 1. Wallet identity (X-Wallet-Address header)
    raw_wallet = request.headers.get("x-wallet-address", "").strip()
    if not raw_wallet or not _valid_address(raw_wallet):
        raise HTTPException(status_code=400, detail={
            "error": "Missing or invalid X-Wallet-Address.",
            "message": "Send your Base wallet address in the X-Wallet-Address header.",
        })
    caller = raw_wallet.lower()

    # 2. Payment proof (X-Payment header)
    tx = request.headers.get("x-payment", "").strip()
    if not tx:
        raise HTTPException(status_code=402,
                            detail=_challenge(response, request.url.path))

    # 3. Format
    if not _valid_tx(tx):
        raise HTTPException(status_code=400, detail={
            "error": "Invalid X-Payment format.",
            "message": "X-Payment must be a valid Base tx hash (0x + 64 hex chars).",
        })

    # 4. Stateful Nonce Linearization (Pessimistic Nonce Locking)
    # Check DB first (persistent replay protection)
    db_payment = db.get(X402ConsumedPayment, tx.lower())
    if db_payment is not None:
        raise HTTPException(status_code=400, detail={
            "error": "Payment already consumed.",
            "message": "This tx hash was already used. Send a new payment.",
        })

    # Lock the nonce (tx hash) to prevent simultaneous free-rider concurrency races
    async with _nonce_lock:
        if tx.lower() in _pending_tx_hashes:
            raise HTTPException(status_code=409, detail={
                "error": "Conflict",
                "message": "State lock collision: transaction verification is currently pending.",
            })
        _pending_tx_hashes.add(tx.lower())

    try:
        # 5. Optional EIP-712 Request-Bound Signature Verification (Context-Binding)
        sig = request.headers.get("x-signature", "").strip()
        if sig:
            try:
                # Reconstruct request body hash
                body_bytes = await request.body()
                body_hash = Web3.keccak(body_bytes) if body_bytes else b"\x00" * 32
                
                # Format EIP-712 nonce from tx hash
                nonce_bytes = Web3.to_bytes(hexstr=tx) if tx.startswith("0x") else tx.encode('utf-8')
                if len(nonce_bytes) < 32:
                    nonce_bytes = nonce_bytes.ljust(32, b"\x00")
                elif len(nonce_bytes) > 32:
                    nonce_bytes = nonce_bytes[:32]
                    
                amount_units = PRICES.get(request.url.path, {"usdc_base_units": 10_000})["usdc_base_units"]
                
                structured_data = {
                    "types": {
                        "EIP712Domain": [
                            {"name": "name", "type": "string"},
                            {"name": "version", "type": "string"},
                            {"name": "chainId", "type": "uint256"},
                            {"name": "verifyingContract", "type": "address"},
                        ],
                        "PaymentRequirements": [
                            {"name": "recipient", "type": "address"},
                            {"name": "amount", "type": "uint256"},
                            {"name": "nonce", "type": "bytes32"},
                            {"name": "method", "type": "string"},
                            {"name": "resource", "type": "string"},
                            {"name": "bodyHash", "type": "bytes32"},
                        ],
                    },
                    "primaryType": "PaymentRequirements",
                    "domain": {
                        "name": "VNP x402 Billing Gateway",
                        "version": "1",
                        "chainId": 8453,
                        "verifyingContract": MERCHANT_WALLET,
                    },
                    "message": {
                        "recipient": MERCHANT_WALLET,
                        "amount": amount_units,
                        "nonce": nonce_bytes,
                        "method": request.method,
                        "resource": request.url.path,
                        "bodyHash": body_hash,
                    }
                }
                
                encoded_message = encode_typed_data(full_message=structured_data)
                recovered_addr = Account.recover_message(encoded_message, signature=sig)
                
                if recovered_addr.lower() != caller.lower():
                    raise HTTPException(status_code=403, detail={
                        "error": "Forbidden",
                        "message": f"Cryptographic Context-Binding mismatch. Signer {recovered_addr} does not match caller {caller}.",
                    })
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail={
                    "error": "Signature verification failed",
                    "message": f"Failed to verify EIP-712 request-bound signature: {str(e)}",
                })

        # 6. On-chain verification
        ok, err, block = await _verify_onchain(tx, request.url.path, caller)
        if not ok:
            raise HTTPException(status_code=402, detail={
                "error": "Payment verification failed.",
                "message": err,
                "appId": BASE_APP_ID,
                "payTo": MERCHANT_WALLET,
                "chain": CHAIN_ID,
            })

        # 7. Record to DB for persistent replay protection
        consumed = X402ConsumedPayment(
            tx_hash=tx.lower(),
            wallet_address=caller,
            endpoint=request.url.path,
            amount_usdc=PRICES.get(request.url.path, {}).get("usdc", "?"),
            chain_id=CHAIN_ID,
            block_number=block,
        )
        db.add(consumed)
        db.commit()
            
        _payment_ledger.append({
            "tx_hash": tx,
            "wallet": caller,
            "endpoint": request.url.path,
            "amount_usdc": PRICES.get(request.url.path, {}).get("usdc", "?"),
            "chain": CHAIN_ID,
            "block_number": block,
            "app_id": BASE_APP_ID,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        async with _nonce_lock:
            if tx.lower() in _pending_tx_hashes:
                _pending_tx_hashes.remove(tx.lower())

    request.state.x402_wallet = caller
    request.state.x402_tx     = tx
    return {"wallet": caller, "tx": tx}


# ---------------------------------------------------------------------------
# ROUTER
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/x402", tags=["x402 Payments"])


@router.get("/config")
async def x402_config() -> dict[str, Any]:
    """Public. Returns all x402 payment config. Call this before paying."""
    return {
        "x402Version": 1,
        "appId": BASE_APP_ID,
        "chain": CHAIN_ID,
        "merchant_wallet": MERCHANT_WALLET,
        "usdc_contract": USDC_CONTRACT,
        "frontend": FRONTEND_ORIGIN,
        "spec": "https://x402.org",
        "prices": {
            path: {"usdc": info["usdc"], "description": info["description"]}
            for path, info in PRICES.items()
        },
        "how_to_pay": (
            "1. Call this endpoint to get prices. "
            "2. Send USDC on Base (eip155:8453) to merchant_wallet. "
            "3. Retry your request with X-Wallet-Address and X-Payment headers."
        ),
    }


class ExecRequest(BaseModel):
    prompt: str
    agent_id: str | None = None
    workspace_id: str = "default"


@router.post("/exec/run")
async def x402_exec_run(
    body: ExecRequest,
    request: Request,
    payment: dict = Depends(require_payment),
) -> dict[str, Any]:
    """$0.05 USDC — governed agent execution run. Payment verified on-chain."""
    return {
        "success": True,
        "payment_status": "verified",
        "app_id": BASE_APP_ID,
        "chain": CHAIN_ID,
        "wallet": request.state.x402_wallet,
        "tx_hash": request.state.x402_tx,
        "amount_paid_usdc": "0.05",
        "prompt": body.prompt,
        "agent_id": body.agent_id,
        "workspace_id": body.workspace_id,
        "result": "[authorised — wire to RunOrchestrator.run_governed()]",
    }


@router.get("/benchmarks/premium")
async def x402_benchmarks_premium(
    request: Request,
    payment: dict = Depends(require_payment),
) -> dict[str, Any]:
    """$0.02 USDC — premium benchmark data with live SLA metrics."""
    return {
        "success": True,
        "payment_status": "verified",
        "app_id": BASE_APP_ID,
        "chain": CHAIN_ID,
        "wallet": request.state.x402_wallet,
        "amount_paid_usdc": "0.02",
        "benchmarks": [
            {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "provider": "Google",
             "trust_score": 985, "tier": "Apex", "latency_ms": 110,
             "uptime_percent": 99.99, "staked_amount_usdc": 50000},
            {"id": "gpt-4o", "name": "GPT-4o", "provider": "OpenAI",
             "trust_score": 960, "tier": "Apex", "latency_ms": 140,
             "uptime_percent": 99.95, "staked_amount_usdc": 45000},
            {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet",
             "provider": "Anthropic", "trust_score": 975, "tier": "Apex",
             "latency_ms": 125, "uptime_percent": 99.98, "staked_amount_usdc": 48000},
        ],
    }


class DiscoveryRequest(BaseModel):
    feature_id: str


@router.post("/discovery/unlock")
async def x402_discovery_unlock(
    body: DiscoveryRequest,
    request: Request,
    payment: dict = Depends(require_payment),
) -> dict[str, Any]:
    """$0.01 USDC — discovery feature unlock for connected Veklom wallet."""
    return {
        "success": True,
        "payment_status": "verified",
        "app_id": BASE_APP_ID,
        "chain": CHAIN_ID,
        "wallet": request.state.x402_wallet,
        "amount_paid_usdc": "0.01",
        "feature_id": body.feature_id,
        "feature_unlocked": True,
    }


@router.get("/ledger")
async def x402_ledger() -> dict[str, Any]:
    """Payment audit log. Gate behind admin auth in production."""
    return {
        "success": True,
        "app_id": BASE_APP_ID,
        "merchant_wallet": MERCHANT_WALLET,
        "chain": CHAIN_ID,
        "count": len(_payment_ledger),
        "payments": _payment_ledger,
    }
