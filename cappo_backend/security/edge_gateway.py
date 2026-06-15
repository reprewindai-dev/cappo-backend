"""Edge Gateway — EAT enforcement boundary.

Forward-constructed from :mod:`cappo_backend.security.mcp_gateway`.  The MCP
gateway enforces proof-derived authority via ExecutionIdentityV1; the edge
gateway enforces the downstream Execution Authorization Token (EAT) at the
edge MCP boundary.

Rejection contract:
    HTTP 403 ``{"error":"EAT_VERIFICATION_FAILED","detail":"…","rule":"V…"}``
    logged to the audit service as ``operation_type="law0_violation"``.

Ten verification rules (V1 – V10) are evaluated in order; the first failure
short-circuits with a descriptive error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cappo_backend.config import Settings, get_settings
from cappo_backend.security.nonce_cache import NonceBackend
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json, verify_signature
from cappo_backend.services.eat_builder import eat_canonical_body

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class EATVerificationError(Exception):
    """One or more of the ten EAT verification rules failed.

    Attributes:
        detail: human-readable description of the failure.
        rule:   identifier of the failing rule (e.g. ``"V1"``).
    """

    def __init__(self, detail: str, rule: str) -> None:
        self.detail = detail
        self.rule = rule
        super().__init__(f"[{rule}] {detail}")


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

class EdgeGateway:
    """Enforcement boundary for Execution Authorization Tokens."""

    def __init__(
        self,
        audit: AuditService,
        *,
        eat_signing_key: str,
        nonce_cache: NonceBackend,
        audience: str = "cappo-edge-mcp",
        settings: Settings | None = None,
    ) -> None:
        self._audit = audit
        self._eat_signing_key = eat_signing_key
        self._nonce_cache = nonce_cache
        self._audience = audience
        self._settings = settings or get_settings()

    def require_eat(
        self,
        eat: dict[str, Any] | None,
        *,
        action: str = "",
        action_cost_cents: int = 0,
    ) -> None:
        """Validate an EAT per the ten-rule verification contract.

        Raises :class:`EATVerificationError` on the first failing rule.
        The failure is logged to the audit service as a LAW 0 violation.
        """
        if eat is None:
            self._reject("execution authorization token is missing", "V0")

        try:
            self._v1_signature(eat)
            self._v2_hash(eat)
            self._v3_expiry(eat)
            self._v4_nonce(eat)
            self._v5_directive(eat)
            self._v6_scope(eat, action)
            self._v7_budget(eat, action_cost_cents)
            # V8 (single_use) is enforced by V4 (nonce replay protection).
            # A single-use token's nonce is consumed on first presentation;
            # any subsequent presentation triggers the V4 replay rejection.
            self._v9_version(eat)
            self._v10_audience(eat)
        except EATVerificationError as exc:
            self._audit.record_law0_violation(
                exc.detail,
                extra={"rule": exc.rule, "eat_id": (eat or {}).get("eat_id")},
            )
            raise

    # ------------------------------------------------------------------
    # Verification rules (V1 – V10)
    # ------------------------------------------------------------------

    def _v1_signature(self, eat: dict[str, Any]) -> None:
        """V1 — HMAC signature over canonical body must verify."""
        body = eat_canonical_body(eat)
        if not verify_signature(body, eat.get("signature", ""), self._eat_signing_key):
            self._reject("signature verification failed", "V1")

    def _v2_hash(self, eat: dict[str, Any]) -> None:
        """V2 — SHA-256 hash of canonical body must match."""
        expected = sha256_json(eat_canonical_body(eat))
        if eat.get("hash") != expected:
            self._reject("hash verification failed", "V2")

    def _v3_expiry(self, eat: dict[str, Any]) -> None:
        """V3 — temporal.expires_at must be in the future."""
        temporal = eat.get("temporal") or {}
        expires_raw = temporal.get("expires_at")
        if not expires_raw:
            self._reject("temporal.expires_at is missing", "V3")
        try:
            if isinstance(expires_raw, str):
                expires = datetime.fromisoformat(expires_raw)
            elif isinstance(expires_raw, datetime):
                expires = expires_raw
            else:
                self._reject(
                    f"temporal.expires_at has unexpected type: {type(expires_raw)}",
                    "V3",
                )
                return  # unreachable; keeps type-checker happy
        except ValueError:
            self._reject(
                f"temporal.expires_at is not a valid datetime: {expires_raw!r}",
                "V3",
            )
            return
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            self._reject("execution authorization token has expired", "V3")

    def _v4_nonce(self, eat: dict[str, Any]) -> None:
        """V4 — nonce must not have been seen before (replay protection)."""
        nonce = eat.get("nonce")
        if not nonce:
            self._reject("nonce is missing", "V4")
        temporal = eat.get("temporal") or {}
        ttl = temporal.get("ttl_seconds") or 300
        if self._nonce_cache.check_and_store(nonce, int(ttl)):
            self._reject("nonce has already been consumed (replay detected)", "V4")

    def _v5_directive(self, eat: dict[str, Any]) -> None:
        """V5 — authorization.directive must permit execution."""
        auth = eat.get("authorization") or {}
        directive = auth.get("directive")
        if directive not in ("ALLOW", "ALLOW_WITH_AUDIT"):
            self._reject(
                f"directive {directive!r} does not permit execution", "V5"
            )

    def _v6_scope(self, eat: dict[str, Any], action: str) -> None:
        """V6 — action must be covered by authorization.scope.tools."""
        if not action:
            return
        auth = eat.get("authorization") or {}
        scope = auth.get("scope") or {}
        tools = scope.get("tools") or []
        if tools and action not in tools:
            self._reject(
                f"scope does not cover action {action!r} (allowed: {tools!r})",
                "V6",
            )

    def _v7_budget(self, eat: dict[str, Any], cost_cents: int) -> None:
        """V7 — budget must cover the action cost."""
        if cost_cents <= 0:
            return
        auth = eat.get("authorization") or {}
        budget = auth.get("budget_approved_cents") or 0
        if budget < cost_cents:
            self._reject(
                f"budget insufficient: approved={budget} cents, cost={cost_cents} cents",
                "V7",
            )

    def _v9_version(self, eat: dict[str, Any]) -> None:
        """V9 — eat_version must be '1.0'."""
        version = eat.get("eat_version")
        if version != "1.0":
            self._reject(f"unknown eat_version {version!r}", "V9")

    def _v10_audience(self, eat: dict[str, Any]) -> None:
        """V10 — audience must match the gateway's expected audience."""
        audience = eat.get("audience")
        if audience != self._audience:
            self._reject(
                f"audience mismatch: expected {self._audience!r}, got {audience!r}",
                "V10",
            )

    # ------------------------------------------------------------------
    # Rejection
    # ------------------------------------------------------------------

    @staticmethod
    def _reject(detail: str, rule: str) -> None:
        raise EATVerificationError(detail, rule)
