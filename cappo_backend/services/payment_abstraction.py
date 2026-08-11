"""Payment Protocol Abstraction Layer for CAPPO.

CAPPO accepts commercial admission from multiple payment schemes.
This module normalizes them into a single CommercialAdmission envelope.

Architecture Invariant
──────────────────────
  Payment satisfies COMMERCIAL admission.
  CAPPO independently determines GOVERNANCE admission.
  Payment ≠ authorization.

A paid request that fails governance is still refused by CAPPO.
A refused request still produces an evidence record (denial receipt).

Supported Schemes
─────────────────
  x402 V2
    Headers used:
      Server→Client:  Payment-Required      (402 challenge, JSON body)
      Client→Server:  Payment-Signature     (proof of payment)
      Server→Client:  Payment-Response      (settlement receipt)
    References:
      https://x402.org
      https://github.com/coinbase/x402

  MPP — Machine Payments Protocol
    Co-authored by Tempo and Stripe.
    Supports stablecoins, cards, Bitcoin Lightning.
    Interoperates with x402 services.
    References:
      https://developers.cloudflare.com/payments/mpp/ (when GA)

  Future schemes can be added by implementing PaymentSchemeVerifier and
  registering them in VERIFIERS below. CAPPO authorization code never needs
  to change — it only sees CommercialAdmission.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import Request

logger = logging.getLogger("cappo.payment_abstraction")


# ---------------------------------------------------------------------------
# Commercial Admission envelope — the single object CAPPO sees
# ---------------------------------------------------------------------------

class PaymentScheme(str, Enum):
    X402 = "x402"
    MPP = "mpp"
    INTERNAL = "internal"   # Operator API key — bypasses commercial gate
    NONE = "none"           # No payment scheme detected


@dataclass(frozen=True)
class CommercialAdmission:
    """Normalized result of payment verification.

    CAPPO's authorization logic receives this and makes its own independent
    governance decision. The scheme field is for audit only; governance logic
    must not branch on it.

    Fields
    ------
    satisfied : bool
        True if commercial admission is satisfied (payment verified OR internal
        operator credential). False if payment was required but not satisfied.

    scheme : PaymentScheme
        Which scheme produced this admission. For audit/telemetry only.

    amount_usd : float | None
        Claimed payment amount in USD (if available from scheme metadata).
        CAPPO should not trust this for governance — verify against route config.

    receipt_hash : str | None
        SHA-256 of the raw payment proof bytes. Stable identifier for the
        payment evidence record in PGL.

    wallet_address : str | None
        Payer's EVM wallet address (x402) or equivalent identifier (MPP).

    raw_proof : dict[str, Any]
        Scheme-specific proof data for audit logging. Not for governance logic.

    error : str | None
        If satisfied=False, human-readable reason.
    """
    satisfied: bool
    scheme: PaymentScheme
    amount_usd: float | None = None
    receipt_hash: str | None = None
    wallet_address: str | None = None
    raw_proof: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def unsatisfied(cls, scheme: PaymentScheme, error: str) -> CommercialAdmission:
        return cls(satisfied=False, scheme=scheme, error=error)

    @classmethod
    def internal_operator(cls) -> CommercialAdmission:
        """Operator API key — bypasses commercial gate entirely."""
        return cls(satisfied=True, scheme=PaymentScheme.INTERNAL)


# ---------------------------------------------------------------------------
# Scheme verifier interface
# ---------------------------------------------------------------------------

class PaymentSchemeVerifier:
    """Abstract verifier for a single payment scheme."""

    def can_handle(self, request: Request) -> bool:
        """Return True if the request carries this scheme's headers/tokens."""
        raise NotImplementedError

    async def verify(self, request: Request) -> CommercialAdmission:
        """Verify the payment proof and return a CommercialAdmission."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# x402 V2 verifier
# ---------------------------------------------------------------------------

class X402V2Verifier(PaymentSchemeVerifier):
    """Verifies x402 V2 Payment-Signature header.

    x402 V2 headers:
      Payment-Required     (server → client, 402 challenge)
      Payment-Signature    (client → server, proof of payment)
      Payment-Response     (server → client, settlement receipt)

    The Payment-Signature value is a Base64url-encoded JSON object containing:
      {
        "x402Version": 2,
        "scheme": "exact",
        "network": "eip155:8453",
        "payload": { ... scheme-specific data ... }
      }

    CAPPO's role here is to verify the signature was issued against a
    facilitator receipt, NOT to be the facilitator itself.
    """

    HEADER = "payment-signature"   # HTTP headers are lowercased by ASGI

    def can_handle(self, request: Request) -> bool:
        return self.HEADER in request.headers

    async def verify(self, request: Request) -> CommercialAdmission:
        raw_header = request.headers.get(self.HEADER, "")
        if not raw_header:
            return CommercialAdmission.unsatisfied(
                PaymentScheme.X402, "Missing Payment-Signature header"
            )

        # Hash the raw proof bytes for stable PGL evidence reference
        receipt_hash = hashlib.sha256(raw_header.encode()).hexdigest()

        try:
            import base64
            # Payment-Signature is Base64url-encoded JSON
            padded = raw_header + "=" * (-len(raw_header) % 4)
            decoded = base64.urlsafe_b64decode(padded)
            proof = json.loads(decoded)
        except Exception as exc:
            return CommercialAdmission.unsatisfied(
                PaymentScheme.X402,
                f"Malformed Payment-Signature: {exc}",
            )

        # Extract wallet/amount from payload if present (scheme-specific)
        payload = proof.get("payload", {})
        wallet = payload.get("from") or payload.get("from_address")
        amount = None
        try:
            # ERC-3009 exact scheme: amount is in USDC atomic units (6 decimals)
            raw_amount = payload.get("amount") or payload.get("value")
            if raw_amount is not None:
                amount = int(raw_amount) / 1_000_000  # Convert to USD
        except (ValueError, TypeError):
            pass

        # TODO Phase 5: verify proof against facilitator (x402.org or Coinbase)
        # For now: structural validation only. Full verification requires
        # a facilitator client call.
        logger.debug(
            "x402 V2 payment proof received hash=%s network=%s",
            receipt_hash[:12],
            proof.get("network"),
        )

        return CommercialAdmission(
            satisfied=True,
            scheme=PaymentScheme.X402,
            amount_usd=amount,
            receipt_hash=receipt_hash,
            wallet_address=wallet,
            raw_proof=proof,
        )


# ---------------------------------------------------------------------------
# MPP (Machine Payments Protocol) verifier — Cloudflare/Stripe/Tempo
# ---------------------------------------------------------------------------

class MPPVerifier(PaymentSchemeVerifier):
    """Verifies Machine Payments Protocol (MPP) payment proofs.

    MPP is co-authored by Tempo and Stripe and supports:
      - Stablecoins (USDC, EURC)
      - Traditional cards (via Stripe)
      - Bitcoin Lightning

    MPP is designed to interoperate with x402 services.
    MPP headers (names TBC pending final spec):
      Mpp-Payment-Proof     (client → server, payment proof token)
      Mpp-Amount            (claimed amount for logging)
      Mpp-Currency          (currency code)

    NOTE: MPP is in early-access as of 2026-08. Header names and proof
    format will be updated as the spec stabilizes. This verifier performs
    structural validation only until Cloudflare's MPP verification SDK
    becomes generally available.
    """

    HEADER = "mpp-payment-proof"

    def can_handle(self, request: Request) -> bool:
        return self.HEADER in request.headers

    async def verify(self, request: Request) -> CommercialAdmission:
        raw_proof = request.headers.get(self.HEADER, "")
        if not raw_proof:
            return CommercialAdmission.unsatisfied(
                PaymentScheme.MPP, "Missing Mpp-Payment-Proof header"
            )

        receipt_hash = hashlib.sha256(raw_proof.encode()).hexdigest()
        currency = request.headers.get("mpp-currency", "USDC")
        amount = None
        try:
            raw_amount = request.headers.get("mpp-amount")
            if raw_amount:
                amount = float(raw_amount)
        except (ValueError, TypeError):
            pass

        logger.debug(
            "MPP payment proof received hash=%s currency=%s",
            receipt_hash[:12],
            currency,
        )

        # TODO: verify against Cloudflare MPP SDK when GA
        return CommercialAdmission(
            satisfied=True,
            scheme=PaymentScheme.MPP,
            amount_usd=amount,
            receipt_hash=receipt_hash,
            raw_proof={"proof_token": raw_proof[:64] + "...", "currency": currency},
        )


# ---------------------------------------------------------------------------
# Registry + resolution
# ---------------------------------------------------------------------------

VERIFIERS: list[PaymentSchemeVerifier] = [
    X402V2Verifier(),
    MPPVerifier(),
]


def _is_internal_operator(request: Request, api_key_set: frozenset[str]) -> bool:
    """Check if request carries a valid internal operator credential."""
    key = (
        request.headers.get("x-uacp-internal-key")
        or request.headers.get("x-api-key")
        or ""
    )
    return bool(key and key in api_key_set)


async def resolve_commercial_admission(
    request: Request,
    api_key_set: frozenset[str],
) -> CommercialAdmission:
    """Resolve which payment scheme is present and verify it.

    Resolution order:
      1. Internal operator credential → bypass commercial gate
      2. x402 V2 Payment-Signature header
      3. MPP Mpp-Payment-Proof header
      4. No scheme → unsatisfied (caller may issue 402 challenge)

    Returns a CommercialAdmission. CAPPO makes its own governance decision
    independently afterward.
    """
    if _is_internal_operator(request, api_key_set):
        return CommercialAdmission.internal_operator()

    for verifier in VERIFIERS:
        if verifier.can_handle(request):
            admission = await verifier.verify(request)
            logger.info(
                "commercial_admission satisfied=%s scheme=%s hash=%s",
                admission.satisfied,
                admission.scheme,
                admission.receipt_hash,
            )
            return admission

    return CommercialAdmission.unsatisfied(
        PaymentScheme.NONE,
        "No recognized payment scheme header found",
    )
