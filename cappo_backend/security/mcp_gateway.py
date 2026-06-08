"""MCP Gateway — LAW 0 enforcement boundary.

Forward-constructed (migration note §4). The old backend's ``ZeroTrustMiddleware``
provided auth-only checks at a single choke point. The CAPPO gateway inherits that
choke-point shape but enforces **proof-derived authority** via nine validation rules
(EI Implementation Plan §MCP Gateway validation contract).

Rejection contract (EI Plan §Rejection behavior):
    HTTP 403 ``{"error":"EXECUTION_IDENTITY_REQUIRED","detail":"…","law0":true}``
    logged to the audit service as ``operation_type="law0_violation"``.

The kill-switch/budget 402 takes precedence (§7). The gateway is invoked *after*
auth/entitlement/budget checks have passed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cappo_backend.config import Settings, get_settings
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json, verify_signature
from cappo_backend.services.ei_builder import canonical_body


class EIValidationError(Exception):
    """One or more of the nine EI validation rules failed.

    Attributes:
        detail: human-readable description of the failure.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class MCPGateway:
    """Enforcement boundary for ``ExecutionIdentityV1``."""

    def __init__(
        self,
        audit: AuditService,
        *,
        pgl_lookup: Any | None = None,
        revocation_lookup: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._audit = audit
        self._settings = settings or get_settings()
        # Callable (certificate_id -> PGLCertificate|None) used for rule 1/2.
        self._pgl_lookup = pgl_lookup
        # Callable (execution_id -> bool) used for rule 9 (DB-backed revocation).
        self._revocation_lookup = revocation_lookup

    def require_execution_identity(
        self,
        identity: dict[str, Any] | None,
        *,
        action: str = "",
        action_cost_cents: int = 0,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Validate an ``ExecutionIdentityV1`` per the nine-rule contract.

        Raises :class:`EIValidationError` on the first failing rule. The failure
        is logged to the audit service as a LAW 0 violation.
        """
        if identity is None:
            self._reject("execution identity is missing", workspace_id=workspace_id, run_id=run_id)

        try:
            self._rule_1_persisted_pgl(identity)
            self._rule_2_hash_alignment(identity)
            self._rule_3_directive(identity)
            self._rule_4_ttl(identity)
            self._rule_5_scope(identity, action)
            self._rule_6_budget(identity, action_cost_cents)
            self._rule_7_delegation_depth(identity)
            self._rule_8_signature_hash(identity)
            self._rule_9_not_revoked(identity)
        except EIValidationError as exc:
            self._audit.record_law0_violation(
                exc.detail, workspace_id=workspace_id, run_id=run_id
            )
            raise

    # ------------------------------------------------------------------
    # Validation rules (EI Plan §Validation rules)
    # ------------------------------------------------------------------

    def _rule_1_persisted_pgl(self, ei: dict[str, Any]) -> None:
        """Rule 1 — pgl_pre_certificate_id resolves to a real, persisted cert."""
        cert_id = ei.get("pgl_pre_certificate_id")
        if not cert_id:
            self._reject("pgl_pre_certificate_id is missing")
        if self._pgl_lookup is not None:
            cert = self._pgl_lookup(cert_id)
            if cert is None:
                self._reject(f"PGL certificate {cert_id} not found")
            if not getattr(cert, "persisted", True):
                self._reject(f"PGL certificate {cert_id} is not persisted")

    def _rule_2_hash_alignment(self, ei: dict[str, Any]) -> None:
        """Rule 2 — genome/constitution/plan hashes match PGL cert."""
        if self._pgl_lookup is None:
            return
        cert_id = ei.get("pgl_pre_certificate_id")
        cert = self._pgl_lookup(cert_id) if cert_id else None
        if cert is None:
            return  # already rejected by rule 1
        for field in ("genome_hash", "constitution_hash", "plan_hash"):
            ei_val = ei.get(field)
            cert_val = getattr(cert, field, None)
            if ei_val and cert_val and ei_val != cert_val:
                self._reject(f"{field} mismatch: EI={ei_val!r}, cert={cert_val!r}")

    def _rule_3_directive(self, ei: dict[str, Any]) -> None:
        """Rule 3 — SEKED directive permits execution."""
        directive = ei.get("directive")
        if directive not in ("ALLOW", "ALLOW_WITH_AUDIT"):
            self._reject(f"directive {directive!r} does not permit execution")

    def _rule_4_ttl(self, ei: dict[str, Any]) -> None:
        """Rule 4 — expires_at is in the future."""
        expires_raw = ei.get("expires_at")
        if not expires_raw:
            self._reject("expires_at is missing")
        try:
            if isinstance(expires_raw, str):
                expires = datetime.fromisoformat(expires_raw)
            elif isinstance(expires_raw, datetime):
                expires = expires_raw
            else:
                self._reject(f"expires_at has unexpected type: {type(expires_raw)}")
                return
        except ValueError:
            self._reject(f"expires_at is not a valid datetime: {expires_raw!r}")
            return
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            self._reject("execution identity has expired")

    def _rule_5_scope(self, ei: dict[str, Any], action: str) -> None:
        """Rule 5 — scope covers the requested tool/action."""
        scope = ei.get("scope") or {}
        if not action:
            return
        tools = scope.get("tools") or []
        if tools and action not in tools:
            self._reject(f"scope does not cover action {action!r} (allowed: {tools!r})")

    def _rule_6_budget(self, ei: dict[str, Any], cost_cents: int) -> None:
        """Rule 6 — budget covers action cost."""
        if cost_cents <= 0:
            return
        budget = ei.get("budget_approved_cents") or 0
        if budget < cost_cents:
            self._reject(
                f"budget insufficient: approved={budget} cents, cost={cost_cents} cents"
            )

    def _rule_7_delegation_depth(self, ei: dict[str, Any]) -> None:
        """Rule 7 — delegation depth within configured max."""
        depth = ei.get("delegation_depth") or 0
        if depth > self._settings.max_delegation_depth:
            self._reject(
                f"delegation depth {depth} exceeds max {self._settings.max_delegation_depth}"
            )

    def _rule_8_signature_hash(self, ei: dict[str, Any]) -> None:
        """Rule 8 — signature and hash verify against signing key."""
        expected_hash = sha256_json(canonical_body(ei))
        if ei.get("hash") != expected_hash:
            self._reject("hash verification failed")
        if not verify_signature(
            canonical_body(ei),
            ei.get("signature", ""),
            self._settings.ei_signing_key,
        ):
            self._reject("signature verification failed")

    def _rule_9_not_revoked(self, ei: dict[str, Any]) -> None:
        """Rule 9 — identity is not revoked.

        Checks the in-object ``revoked`` flag *and* (when wired) the
        ``execution_identities`` table via ``revocation_lookup``. Revocation is
        post-issuance mutable state, so the durable row is authoritative — an
        attacker cannot un-revoke by stripping the flag from a replayed object.
        """
        if ei.get("revoked"):
            self._reject("execution identity has been revoked")
        if self._revocation_lookup is not None:
            execution_id = ei.get("execution_id")
            if execution_id and self._revocation_lookup(execution_id):
                self._reject(f"execution identity {execution_id} has been revoked")

    # ------------------------------------------------------------------
    # Rejection
    # ------------------------------------------------------------------

    @staticmethod
    def _reject(
        detail: str,
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        raise EIValidationError(detail)
