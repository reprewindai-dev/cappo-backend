"""Canonical Execution Authorization Token (EAT) builder.

Constructs an EAT from a minted ExecutionIdentityV1 and agent-level identity
claims (certificate, trust score, risk tier).  The EAT is the edge-side
credential: it carries a single-use nonce for replay protection and an
audience claim so a given token is bound to a specific MCP boundary.

The builder is pure and deterministic: given identical inputs (plus the same
clock) it produces an identical object, ``hash``, and ``signature``.  Missing
or invalid inputs fail loudly (``MissingEATInputError``) so a malformed token
can never be silently minted.

Signing uses the same :class:`Signer` protocol as
:mod:`cappo_backend.services.ei_builder` — production can swap HMAC for
asymmetric/KMS without touching this module.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from cappo_backend.services.canonical import sha256_json

# ---------------------------------------------------------------------------
# Signer protocol (identical contract to ei_builder.Signer)
# ---------------------------------------------------------------------------

class Signer(Protocol):
    def sign(self, payload: Any) -> str: ...
    def verify(self, payload: Any, signature: str) -> bool: ...


# ---------------------------------------------------------------------------
# Validation error
# ---------------------------------------------------------------------------

class MissingEATInputError(ValueError):
    """Raised when an EAT input is missing, empty, or out of policy."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TTL_SECONDS: int = 600


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

@dataclass
class EATBuilder:
    """Assemble, hash, and sign an Execution Authorization Token."""

    signer: Signer
    default_ttl_seconds: int = 300
    max_ttl_seconds: int = MAX_TTL_SECONDS
    _clock: Any = field(default=None, repr=False)

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.now(timezone.utc)

    # ---- public API -------------------------------------------------------

    def build(
        self,
        *,
        execution_identity: dict[str, Any],
        agent_id: str,
        certificate_id: str,
        trust_score: float,
        risk_tier: str,
        audience: str = "cappo-edge-mcp",
        issuer: str = "cappo-inside-mcp",
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Assemble, hash, and sign a canonical EAT object."""
        self._validate(
            execution_identity=execution_identity,
            agent_id=agent_id,
            trust_score=trust_score,
            risk_tier=risk_tier,
            ttl_seconds=ttl_seconds,
        )

        ttl_seconds = ttl_seconds or self.default_ttl_seconds
        issued_at = self._now()
        expires_at = issued_at + timedelta(seconds=ttl_seconds)

        eat_id = f"eat-{uuid.uuid4()}"
        nonce = os.urandom(32).hex()

        body: dict[str, Any] = {
            "eat_version": "1.0",
            "eat_id": eat_id,
            "execution_id": execution_identity["execution_id"],
            "subject": {
                "agent_id": agent_id,
                "certificate_id": certificate_id,
                "trust_score": trust_score,
                "risk_tier": risk_tier,
            },
            "authorization": {
                "directive": execution_identity["directive"],
                "scope": execution_identity["scope"],
                "budget_approved_cents": execution_identity["budget_approved_cents"],
                "budget_reserve_cents": execution_identity.get("budget_reserve_cents", 0),
                "single_use": True,
            },
            "provenance": {
                "pgl_pre_certificate_id": execution_identity["pgl_pre_certificate_id"],
                "genome_hash": execution_identity["genome_hash"],
                "constitution_hash": execution_identity["constitution_hash"],
                "plan_hash": execution_identity["plan_hash"],
                "governance_decision_hash": sha256_json({
                    "directive": execution_identity["directive"],
                    "risk_tier": execution_identity["risk_tier"],
                }),
            },
            "temporal": {
                "issued_at": _iso(issued_at),
                "expires_at": _iso(expires_at),
                "ttl_seconds": ttl_seconds,
            },
            "issuer": issuer,
            "audience": audience,
            "nonce": nonce,
        }

        eat = dict(body)
        eat["hash"] = sha256_json(body)
        eat["signature"] = self.signer.sign(body)
        return eat

    # ---- validation -------------------------------------------------------

    def _validate(
        self,
        *,
        execution_identity: dict[str, Any] | None,
        agent_id: str,
        trust_score: float,
        risk_tier: str,
        ttl_seconds: int | None,
    ) -> None:
        if execution_identity is None:
            raise MissingEATInputError("execution_identity must not be None")
        if not execution_identity.get("execution_id"):
            raise MissingEATInputError(
                "execution_identity must contain a non-empty 'execution_id'"
            )
        if not agent_id:
            raise MissingEATInputError("agent_id must not be empty")
        if trust_score <= 40:
            raise MissingEATInputError(
                f"trust_score must be > 40, got {trust_score}"
            )
        if risk_tier == "terminated":
            raise MissingEATInputError(
                "risk_tier must not be 'terminated'"
            )
        effective_ttl = ttl_seconds or self.default_ttl_seconds
        if not (1 <= effective_ttl <= self.max_ttl_seconds):
            raise MissingEATInputError(
                f"ttl_seconds must be between 1 and {self.max_ttl_seconds}, "
                f"got {effective_ttl}"
            )


# ---------------------------------------------------------------------------
# Canonical body helper (strips derived fields before hash/signature verify)
# ---------------------------------------------------------------------------

_UNSIGNED_FIELDS = ("hash", "signature")


def eat_canonical_body(eat: dict[str, Any]) -> dict[str, Any]:
    """Return the hashable body (everything except derived fields)."""
    return {k: v for k, v in eat.items() if k not in _UNSIGNED_FIELDS}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)
