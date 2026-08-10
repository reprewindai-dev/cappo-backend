"""Versioned evidence tokens for capability-mount approval gates.

Evidence is valid only for one principal, mount, action, execution nonce and
workspace/project scope, for a short expiry. Replay consumption is persisted by
``MountRegistry`` in the same transaction as the final action decision.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, Literal
from uuid import uuid4

from cappo_backend.capability_mount.models import Mount
from cappo_backend.services.canonical import canonical_json, sign_payload_hmac, verify_signature_hmac

EvidenceKind = Literal["human_approval", "suppression_check"]
TOKEN_VERSION = 1
MAX_EVIDENCE_TTL_SECONDS = 600
MAX_FUTURE_SKEW_SECONDS = 30


@dataclass(frozen=True)
class VerifiedMountEvidence:
    jti: str
    kind: EvidenceKind
    expires_at: datetime


class BoundMountEvidenceVerifier:
    def __init__(
        self,
        *,
        approval_key: str = "",
        suppression_key: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.approval_key = approval_key
        self.suppression_key = suppression_key
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def verify(
        self,
        token: str | None,
        *,
        kind: EvidenceKind,
        principal: str,
        mount: Mount,
        action: str,
        nonce: str,
    ) -> tuple[VerifiedMountEvidence | None, str]:
        key = self.approval_key if kind == "human_approval" else self.suppression_key
        if not key:
            return None, "verification_key_unavailable"
        if not token or not isinstance(token, str):
            return None, "evidence_missing"

        try:
            encoded_payload, signature = token.split(".", 1)
            payload_bytes = _b64decode(encoded_payload)
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            return None, "evidence_malformed"
        if not isinstance(payload, dict):
            return None, "evidence_malformed"
        if canonical_json(payload).encode("utf-8") != payload_bytes:
            return None, "evidence_not_canonical"
        if not verify_signature_hmac(payload, signature, key):
            return None, "evidence_signature_invalid"

        if payload.get("v") != TOKEN_VERSION or payload.get("kind") != kind:
            return None, "evidence_type_invalid"
        expected = {
            "principal": principal,
            "mount_id": mount.id,
            "action": action,
            "nonce_hash": _nonce_hash(nonce),
            "scope_hash": _scope_hash(mount),
        }
        if any(payload.get(field) != value for field, value in expected.items()):
            return None, "evidence_binding_mismatch"

        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti or len(jti) > 255:
            return None, "evidence_jti_invalid"
        try:
            issued_at = _parse_time(payload.get("iat"))
            expires_at = _parse_time(payload.get("exp"))
        except ValueError:
            return None, "evidence_time_invalid"
        now = self._clock().astimezone(timezone.utc)
        if issued_at > now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
            return None, "evidence_not_yet_valid"
        if expires_at <= now:
            return None, "evidence_expired"
        if expires_at <= issued_at:
            return None, "evidence_time_invalid"
        if (expires_at - issued_at).total_seconds() > MAX_EVIDENCE_TTL_SECONDS:
            return None, "evidence_ttl_excessive"

        return VerifiedMountEvidence(jti=jti, kind=kind, expires_at=expires_at), "verified"


def issue_bound_mount_evidence(
    *,
    kind: EvidenceKind,
    signing_key: str,
    principal: str,
    mount: Mount,
    action: str,
    nonce: str,
    ttl_seconds: int = 300,
    jti: str | None = None,
    issued_at: datetime | None = None,
) -> str:
    """Issue evidence for trusted internal producers/tests; this is not an API route."""
    if not signing_key:
        raise ValueError("signing_key is required")
    if ttl_seconds < 1 or ttl_seconds > MAX_EVIDENCE_TTL_SECONDS:
        raise ValueError("invalid evidence ttl")
    now = issued_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "v": TOKEN_VERSION,
        "kind": kind,
        "jti": jti or f"ev_{uuid4().hex}",
        "principal": principal,
        "mount_id": mount.id,
        "action": action,
        "nonce_hash": _nonce_hash(nonce),
        "scope_hash": _scope_hash(mount),
        "iat": now.astimezone(timezone.utc).isoformat(),
        "exp": expires.astimezone(timezone.utc).isoformat(),
    }
    encoded_payload = _b64encode(canonical_json(payload).encode("utf-8"))
    return f"{encoded_payload}.{sign_payload_hmac(payload, signing_key)}"


def _scope_hash(mount: Mount) -> str:
    return sha256(
        canonical_json(
            {"workspace": mount.scope.workspace, "project": mount.scope.project}
        ).encode("utf-8")
    ).hexdigest()


def _nonce_hash(nonce: str) -> str:
    return sha256(nonce.encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
