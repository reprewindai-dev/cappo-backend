"""MCP Gateway — LAW 0 enforcement boundary.

Forward-constructed (migration note §4). Enforces proof-derived authority via 
the updated fail-closed validation contract for ExecutionIdentityV1 and
ExecutionSessionTokenV1.

Rejection contract:
    HTTP 403 {"error":"EXECUTION_IDENTITY_REQUIRED","code":"...","detail":"…","law0":true}
    logged to the audit service as operation_type="law0_violation".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cappo_backend.config import Settings, get_settings
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json, verify_signature_ed25519
from cappo_backend.services.ei_builder import ExecutionSessionTokenVerifier, canonical_body


class EIValidationError(Exception):
    """One or more of the EI validation rules failed.

    Attributes:
        detail: human-readable description of the failure.
        code: protocol-specified error code.
    """

    def __init__(self, detail: str, code: str = "LAW0_EI_INVALID") -> None:
        self.detail = detail
        self.code = code
        super().__init__(detail)


class MCPGateway:
    """Enforcement boundary for ``ExecutionIdentityV1`` and session tokens."""

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
        # Callable (certificate_id -> PGLCertificate|None) used for PGL lookup.
        self._pgl_lookup = pgl_lookup
        # Callable (ei_id -> bool) used for revocation check.
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
        """Validate an ``ExecutionIdentityV1`` per the zero-trust rule contract.

        Raises :class:`EIValidationError` on the first failing rule. The failure
        is logged to the audit service as a LAW 0 violation.
        """
        if identity is None:
            self._reject("execution identity is missing", code="LAW0_EI_INVALID", workspace_id=workspace_id, run_id=run_id)

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
            self._rule_10_context_binding(identity, workspace_id, run_id)
        except EIValidationError as exc:
            self._audit.record_law0_violation(
                exc.detail, workspace_id=workspace_id or identity.get("tenant_id"), run_id=run_id or identity.get("run_id")
            )
            raise

    def require_session_token(
        self,
        token: dict[str, Any] | None,
        parent_ei: dict[str, Any],
        *,
        action: str = "",
        action_cost_cents: int = 0,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Validate an ExecutionSessionTokenV1 against its parent EI."""
        try:
            if token is None:
                self._reject("session token is missing", code="LAW0_EI_INVALID", workspace_id=workspace_id, run_id=run_id)

            # Verify HMAC signature and fields
            verifier = ExecutionSessionTokenVerifier(self._settings.ei_signing_key)
            if not verifier.verify(token, parent_ei):
                self._reject("session token verification failed", code="LAW0_EI_INVALID", workspace_id=workspace_id, run_id=run_id)

            # Check that parent EI is not revoked
            self._rule_9_not_revoked(parent_ei)

            # Enforce action scope
            if action and token["tool_id"] != action:
                self._reject(f"session token tool scope mismatch: token={token['tool_id']}, requested={action}", code="LAW0_POLICY_MISMATCH", workspace_id=workspace_id, run_id=run_id)

            # Enforce budget against parent
            self._rule_6_budget(parent_ei, action_cost_cents)
        except EIValidationError as exc:
            self._audit.record_law0_violation(
                exc.detail, workspace_id=workspace_id or parent_ei.get("tenant_id"), run_id=run_id or parent_ei.get("run_id")
            )
            raise

    # ------------------------------------------------------------------
    # Verification Rules
    # ------------------------------------------------------------------

    def _rule_1_persisted_pgl(self, ei: dict[str, Any]) -> None:
        """Rule 1 — pgl_certificate_id resolves to a real, persisted cert."""
        if "pgl_pre_certificate_id" in ei:
            cert_id = ei.get("pgl_pre_certificate_id")
            if not cert_id:
                self._reject("pgl_pre_certificate_id is missing", code="LAW0_PGL_MISSING")
        else:
            cert_id = ei.get("pgl_certificate_id")
            if not cert_id:
                self._reject("pgl_certificate_id is missing", code="LAW0_PGL_MISSING")

        if self._pgl_lookup is not None:
            cert = self._pgl_lookup(cert_id)
            if cert is None:
                self._reject(f"PGL certificate {cert_id} not found", code="LAW0_PGL_MISSING")
            if not getattr(cert, "persisted", True):
                self._reject(f"PGL certificate {cert_id} is not persisted", code="LAW0_PGL_MISSING")

    def _rule_2_hash_alignment(self, ei: dict[str, Any]) -> None:
        """Rule 2 — genome/constitution/plan hashes match PGL cert."""
        if self._pgl_lookup is None:
            return
        if "pgl_pre_certificate_id" in ei:
            cert_id = ei.get("pgl_pre_certificate_id")
        else:
            cert_id = ei.get("pgl_certificate_id")
        cert = self._pgl_lookup(cert_id) if cert_id else None
        if cert is None:
            return

        # Check standard hashes
        for field in ("genome_hash", "constitution_hash", "plan_hash", "authority_bundle_hash", "policy_hash"):
            ei_val = ei.get(field)
            cert_val = getattr(cert, field, None)
            if ei_val and cert_val and ei_val != cert_val:
                self._reject(f"{field} mismatch: EI={ei_val!r}, cert={cert_val!r}", code="LAW0_POLICY_MISMATCH")

        # Verify run_id matches certificate if not legacy
        if not ei.get("pgl_pre_certificate_id"):
            token_run_id = ei.get("run_id")
            if token_run_id and hasattr(cert, "run_id") and cert.run_id != token_run_id:
                self._reject(f"run_id mismatch: token={token_run_id}, cert={cert.run_id}", code="LAW0_PGL_MISSING")

    def _rule_3_directive(self, ei: dict[str, Any]) -> None:
        """Rule 3 — SEKED directive permits execution."""
        directive = ei.get("directive")
        if directive and directive not in ("ALLOW", "ALLOW_WITH_AUDIT"):
            self._reject(f"directive {directive!r} does not permit execution", code="LAW0_POLICY_MISMATCH")

    def _rule_4_ttl(self, ei: dict[str, Any]) -> None:
        """Rule 4 — expires_at is in the future."""
        expires_raw = ei.get("expires_at")
        if not expires_raw:
            self._reject("expires_at is missing", code="LAW0_EI_INVALID")
        try:
            if isinstance(expires_raw, str):
                expires = datetime.fromisoformat(expires_raw)
            elif isinstance(expires_raw, datetime):
                expires = expires_raw
            else:
                self._reject(f"expires_at has unexpected type: {type(expires_raw)}", code="LAW0_EI_INVALID")
                return
        except ValueError:
            self._reject(f"expires_at is not a valid datetime: {expires_raw!r}", code="LAW0_EI_INVALID")
            return

        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            self._reject("execution identity has expired", code="LAW0_EI_EXPIRED")

    def _rule_5_scope(self, ei: dict[str, Any], action: str) -> None:
        """Rule 5 — scope covers the requested tool/action."""
        if not action:
            return

        capabilities = ei.get("capabilities", [])
        if capabilities:
            # Check capability list
            match_cap = None
            for cap in capabilities:
                if cap["capability_id"] == action:
                    match_cap = cap
                    break
            if not match_cap:
                self._reject(f"scope (capabilities) does not cover requested action: {action}", code="LAW0_POLICY_MISMATCH")
        else:
            # Check legacy scope
            scope = ei.get("scope") or {}
            tools = scope.get("tools") or []
            if tools and action not in tools:
                self._reject(f"scope does not cover action {action!r} (allowed: {tools!r})", code="LAW0_POLICY_MISMATCH")

    def _rule_6_budget(self, ei: dict[str, Any], cost_cents: int) -> None:
        """Rule 6 — budget covers action cost."""
        if cost_cents <= 0:
            return

        # Check total budget limit
        budget = ei.get("budget", {})
        if budget:
            max_spend = budget.get("max_spend", 0.0)
            if max_spend * 100 < cost_cents:
                self._reject(f"budget limit exceeded: max={max_spend} USD, cost={cost_cents / 100.0} USD", code="LAW0_BUDGET_EXCEEDED")

        # Backcompat budget approved check
        legacy_budget = ei.get("budget_approved_cents", 0)
        if legacy_budget > 0 and legacy_budget < cost_cents:
            self._reject(f"budget insufficient: approved={legacy_budget} cents, cost={cost_cents} cents", code="LAW0_BUDGET_EXCEEDED")

    def _rule_7_delegation_depth(self, ei: dict[str, Any]) -> None:
        """Rule 7 — delegation depth within configured max."""
        delegation = ei.get("delegation", {})
        if delegation:
            depth = delegation.get("max_depth", 0)
        else:
            depth = ei.get("delegation_depth", 0)

        if depth > self._settings.max_delegation_depth:
            self._reject(f"delegation depth {depth} exceeds max {self._settings.max_delegation_depth}", code="LAW0_POLICY_MISMATCH")

    def _rule_8_signature_hash(self, ei: dict[str, Any]) -> None:
        """Rule 8 — signature and hash verify against signing key."""
        expected_hash = sha256_json(canonical_body(ei))
        if ei.get("hash") != expected_hash:
            self._reject("hash verification failed", code="LAW0_EI_INVALID")

        sig = ei.get("signature")
        if not sig:
            self._reject("signature is missing", code="LAW0_EI_INVALID")

        if not verify_signature_ed25519(canonical_body(ei), sig, self._settings.ei_signing_key):
            self._reject("signature verification failed", code="LAW0_EI_INVALID")

    def _rule_9_not_revoked(self, ei: dict[str, Any]) -> None:
        """Rule 9 — identity is not revoked."""
        if ei.get("revoked"):
            self._reject("execution identity has been revoked", code="LAW0_EI_REVOKED")
        
        ei_id = ei.get("ei_id") or ei.get("execution_id")
        if ei_id and self._revocation_lookup is not None:
            if self._revocation_lookup(ei_id):
                self._reject(f"execution identity {ei_id} has been revoked", code="LAW0_EI_REVOKED")

    def _rule_10_context_binding(self, ei: dict[str, Any], workspace_id: str | None, run_id: str | None) -> None:
        """Rule 10 — Enforce context bindings to prevent session hijacking."""
        # Match run_id if provided and not legacy
        if not ei.get("pgl_pre_certificate_id"):
            token_run_id = ei.get("run_id") or ei.get("execution_id")
            if run_id and token_run_id and token_run_id != run_id:
                self._reject(f"hijack check: run_id mismatch: token={token_run_id}, request={run_id}", code="LAW0_POLICY_MISMATCH")

        # Match tenant_id/workspace_id if provided
        token_tenant_id = ei.get("tenant_id") or ei.get("workspace_id")
        if workspace_id and token_tenant_id and token_tenant_id != workspace_id:
            self._reject(f"hijack check: tenant_id mismatch: token={token_tenant_id}, request={workspace_id}", code="LAW0_POLICY_MISMATCH")

    # ------------------------------------------------------------------
    # Rejection Helper
    # ------------------------------------------------------------------

    def _reject(
        self,
        detail: str,
        code: str = "LAW0_EI_INVALID",
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        raise EIValidationError(detail, code=code)
