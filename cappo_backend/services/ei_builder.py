"""Canonical ExecutionIdentityV1 and ExecutionSessionTokenV1 builders.

Implements JCS canonicalization, Ed25519 asymmetric signatures for ExecutionIdentityV1,
and symmetric HMAC-SHA256 session tokens with risk-based TTL enforcement.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from cappo_backend.services.canonical import (
    sign_payload_ed25519,
    verify_signature_ed25519,
)

# Fields that must be present (and non-empty) for an EI mint to succeed.
REQUIRED_INPUTS: tuple[str, ...] = (

    "subject",
    "tenant_id",
    "run_id",
    "capabilities",
    "authority_bundle_hash",
    "policy_hash",
    "pgl_certificate_id",
    "delegation",
    "budget",
    "execution_mode",
)


class MissingEIInputError(ValueError):
    """Raised when a required ExecutionIdentityV1 input is missing or empty."""


class Signer(Protocol):
    def sign(self, payload: Any) -> str: ...
    def verify(self, payload: Any, signature: str) -> bool: ...


@dataclass
class Ed25519Signer:
    """Ed25519 (EdDSA) signing adapter using a deterministic private key derived from seed."""

    signing_key: str

    def sign(self, payload: Any) -> str:
        return sign_payload_ed25519(payload, self.signing_key)

    def verify(self, payload: Any, signature: str) -> bool:
        return verify_signature_ed25519(payload, signature, self.signing_key)



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
        """Assemble, JCS canonicalize, and sign a canonical ExecutionIdentityV1 object."""
        self._validate(inputs)

        issued_at = inputs.get("issued_at") or self._now()
        ttl_seconds = int(inputs.get("ttl_seconds") or self.default_ttl_seconds)
        expires_at = inputs.get("expires_at") or (issued_at + timedelta(seconds=ttl_seconds))

        is_legacy = inputs.get("_is_legacy", False)

        if is_legacy:
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
            # Add forward-compat keys
            identity["ei_id"] = body["execution_id"]
            identity["subject"] = inputs["subject"]
            identity["tenant_id"] = inputs.get("tenant_id") or "default"
            identity["run_id"] = body["execution_id"]
            identity["capabilities"] = inputs["capabilities"]
            identity["pgl_certificate_id"] = body["pgl_pre_certificate_id"]
            identity["delegation"] = inputs["delegation"]
            identity["budget"] = inputs["budget"]
            identity["authority_bundle_hash"] = inputs["authority_bundle_hash"]
            identity["policy_hash"] = inputs["policy_hash"]
        else:
            body = {
                "ei_id": inputs.get("ei_id") or inputs.get("execution_id") or str(uuid.uuid4()),
                "subject": inputs["subject"],
                "tenant_id": inputs["tenant_id"],
                "run_id": inputs["run_id"],
                "capabilities": inputs["capabilities"],
                "issued_at": _iso(issued_at),
                "expires_at": _iso(expires_at),
                "authority_bundle_hash": inputs["authority_bundle_hash"],
                "policy_hash": inputs["policy_hash"],
                "pgl_certificate_id": inputs["pgl_certificate_id"],
                "delegation": inputs["delegation"],
                "budget": inputs["budget"],
                "execution_mode": inputs.get("execution_mode", "live"),
            }
            if "agent" in inputs:
                body["agent"] = inputs["agent"]
            identity = dict(body)
            # Add backwards compatibility key support
            identity["execution_id"] = body["ei_id"]
            identity["pgl_pre_certificate_id"] = body["pgl_certificate_id"]
            if inputs.get("directive"):
                identity["directive"] = inputs["directive"]
            identity["risk_tier"] = inputs.get("risk_tier") or "standard"

        return self._finalize_and_sign(identity)

    def _validate(self, inputs: dict[str, Any]) -> None:
        missing = [k for k in REQUIRED_INPUTS if not inputs.get(k)]
        if missing:
            raise ValueError(f"Missing required ExecutionIdentity inputs: {missing}")

    def _finalize_and_sign(self, identity: dict[str, Any]) -> dict[str, Any]:
        """Convert fields to strings where necessary and apply JCS/Ed25519 signature."""
        for k, v in identity.items():
            if isinstance(v, uuid.UUID):
                identity[k] = str(v)
            elif isinstance(v, datetime):
                identity[k] = _iso(v)

        identity["signature"] = self.signer.sign(identity)
        return identity

@dataclass
class ExecutionSessionTokenBuilder:
    signer: Signer
    default_ttl_seconds: int = 30
    _clock: Any = field(default=None, repr=False)

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.now(timezone.utc)

    def build(
        self,
        *,
        parent_ei: dict[str, Any],
        tool_id: str,
        ttl_seconds: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        """Assemble, JCS canonicalize, and HMAC-sign an ExecutionSessionTokenV1."""
        issued_at = self._now()

        # Enforce TTL limits based on Risk Levels
        tool_lower = tool_id.lower()
        if any(x in tool_lower for x in ("read", "view", "get", "status")):
            default_ttl = 60
            max_ttl = 120
        elif any(x in tool_lower for x in ("pay", "write", "delete", "admin", "execute", "run", "http")):
            default_ttl = 15
            max_ttl = 30
        else:
            default_ttl = 30
            max_ttl = 60

        ttl = ttl_seconds or default_ttl
        if ttl > max_ttl:
            ttl = max_ttl

        expires_at = issued_at + timedelta(seconds=ttl)

        # Enforce session.expires_at <= parent_ei.expires_at
        parent_exp_raw = parent_ei.get("expires_at")
        if parent_exp_raw:
            parent_exp = datetime.fromisoformat(parent_exp_raw)
            if expires_at > parent_exp:
                expires_at = parent_exp

        body = {
            "st_id": str(uuid.uuid4()),
            "parent_ei_id": parent_ei["ei_id"],
            "tenant_id": parent_ei["tenant_id"],
            "subject": parent_ei["subject"],
            "tool_id": tool_id,
            "issued_at": _iso(issued_at),
            "expires_at": _iso(expires_at),
            "nonce": nonce or os.urandom(16).hex(),
        }

        token = dict(body)
        from cappo_backend.services.canonical import sign_payload_hmac
        token["signature"] = sign_payload_hmac(body, self.hmac_key)
        return token


@dataclass
class ExecutionSessionTokenVerifier:
    hmac_key: str
    _clock: Any = field(default=None, repr=False)

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.now(timezone.utc)

    def verify(self, token: dict[str, Any], parent_ei: dict[str, Any]) -> bool:
        """Verify the session token against its parent EI and check expiration."""
        required = [
            "st_id",
            "parent_ei_id",
            "tenant_id",
            "subject",
            "tool_id",
            "issued_at",
            "expires_at",
            "nonce",
            "signature",
        ]
        if any(f not in token for f in required):
            return False

        if token["parent_ei_id"] != parent_ei["ei_id"]:
            return False
        if token["tenant_id"] != parent_ei["tenant_id"]:
            return False
        if token["subject"] != parent_ei["subject"]:
            return False

        # Verify capability matches tool_id
        capabilities = parent_ei.get("capabilities", [])
        if not any(c["capability_id"] == token["tool_id"] for c in capabilities):
            return False

        try:
            expires = datetime.fromisoformat(token["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError:
            return False

        if expires <= self._now():
            return False

        body = {k: v for k, v in token.items() if k != "signature"}
        from cappo_backend.services.canonical import verify_signature_hmac
        return verify_signature_hmac(body, token["signature"], self.hmac_key)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)
