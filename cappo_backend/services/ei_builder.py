"""Canonical ExecutionIdentityV1 builder.

Implements the EI field set from the EI Implementation Plan (§ExecutionIdentityV1
fields). The builder is pure and deterministic: given identical inputs it
produces an identical object, ``hash``, and ``signature``. Missing required
inputs fail loudly (``MissingEIInputError``) so a malformed identity can never be
silently minted.

The signature uses an isolated signing adapter (HMAC-SHA256 over canonical JSON).
This is deliberately swappable: the production signing mechanism (e.g. asymmetric
keys / KMS) can replace :class:`HmacSigner` without touching the builder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from cappo_backend.services.canonical import sha256_json, sign_payload, verify_signature

# Fields that must be present (and non-empty) for a mint to succeed.
REQUIRED_INPUTS: tuple[str, ...] = (
    "pgl_pre_certificate_id",
    "genome_hash",
    "constitution_hash",
    "plan_hash",
    "directive",
    "risk_tier",
    "scope",
    "issuer",
)

# Order of fields used to assemble the canonical EI body (hash is computed over
# everything except `hash` and `signature`).
EI_FIELDS: tuple[str, ...] = (
    "execution_id",
    "pgl_pre_certificate_id",
    "pgl_post_certificate_id",
    "genome_hash",
    "constitution_hash",
    "plan_hash",
    "tool_manifest_hash",
    "delegation_chain_hash",
    "input_hash",
    "seked_attestation_hash",
    "directive",
    "risk_tier",
    "budget_approved_cents",
    "budget_reserve_cents",
    "delegation_depth",
    "ttl_seconds",
    "expires_at",
    "scope",
    "human_attestation_hash",
    "ai_attestation_hash",
    "execution_attestation_hash",
    "issuer",
    "issued_at",
)


class MissingEIInputError(ValueError):
    """Raised when a required ExecutionIdentityV1 input is missing or empty."""


class Signer(Protocol):
    def sign(self, payload: Any) -> str: ...
    def verify(self, payload: Any, signature: str) -> bool: ...


@dataclass
class HmacSigner:
    """Isolated HMAC-SHA256 signing adapter.

    Swap this for an asymmetric/KMS-backed signer in production without changing
    the builder contract.
    """

    signing_key: str

    def sign(self, payload: Any) -> str:
        return sign_payload(payload, self.signing_key)

    def verify(self, payload: Any, signature: str) -> bool:
        return verify_signature(payload, signature, self.signing_key)


@dataclass
class ExecutionIdentityBuilder:
    signer: Signer
    default_ttl_seconds: int = 300
    _clock: Any = field(default=None, repr=False)

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.now(timezone.utc)

    def build(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Assemble, hash, and sign a canonical ExecutionIdentityV1 object."""
        self._validate(inputs)

        issued_at = inputs.get("issued_at") or self._now()
        ttl_seconds = int(inputs.get("ttl_seconds") or self.default_ttl_seconds)
        expires_at = inputs.get("expires_at") or (issued_at + timedelta(seconds=ttl_seconds))

        body: dict[str, Any] = {
            "execution_id": inputs.get("execution_id") or str(uuid.uuid4()),
            "pgl_pre_certificate_id": inputs["pgl_pre_certificate_id"],
            "pgl_post_certificate_id": inputs.get("pgl_post_certificate_id"),
            "genome_hash": inputs["genome_hash"],
            "constitution_hash": inputs["constitution_hash"],
            "plan_hash": inputs["plan_hash"],
            "tool_manifest_hash": inputs.get("tool_manifest_hash"),
            "delegation_chain_hash": inputs.get("delegation_chain_hash"),
            "input_hash": inputs.get("input_hash"),
            "seked_attestation_hash": inputs.get("seked_attestation_hash"),
            "directive": inputs["directive"],
            "risk_tier": inputs["risk_tier"],
            "budget_approved_cents": int(inputs.get("budget_approved_cents") or 0),
            "budget_reserve_cents": int(inputs.get("budget_reserve_cents") or 0),
            "delegation_depth": int(inputs.get("delegation_depth") or 0),
            "ttl_seconds": ttl_seconds,
            "expires_at": _iso(expires_at),
            "scope": inputs["scope"],
            "human_attestation_hash": inputs.get("human_attestation_hash"),
            "ai_attestation_hash": inputs.get("ai_attestation_hash"),
            "execution_attestation_hash": inputs.get("execution_attestation_hash"),
            "issuer": inputs["issuer"],
            "issued_at": _iso(issued_at),
        }

        identity = dict(body)
        identity["hash"] = sha256_json(body)
        identity["signature"] = self.signer.sign(body)
        return identity

    def _validate(self, inputs: dict[str, Any]) -> None:
        missing = [
            name
            for name in REQUIRED_INPUTS
            if inputs.get(name) in (None, "", [], {})
        ]
        if missing:
            raise MissingEIInputError(
                f"missing required ExecutionIdentityV1 inputs: {', '.join(sorted(missing))}"
            )


# Fields excluded from the signed/hashed body. ``hash``/``signature`` are
# derived; ``revoked`` is mutable post-issuance state (the identity is signed at
# mint time, revocation happens later) so it must not participate in the hash.
_UNSIGNED_FIELDS = ("hash", "signature", "revoked")


def canonical_body(identity: dict[str, Any]) -> dict[str, Any]:
    """Return the hashable body (everything except derived/mutable fields)."""
    return {k: v for k, v in identity.items() if k not in _UNSIGNED_FIELDS}


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)
