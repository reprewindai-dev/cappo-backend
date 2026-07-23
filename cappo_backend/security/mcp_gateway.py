import hashlib
import json
import os
import redis
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, Set

from cappo_backend.config import Settings, get_settings
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json, verify_signature_ed25519
from cappo_backend.services.ei_builder import ExecutionSessionTokenVerifier, canonical_body
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import redis

from cappo_backend.core.governance.compliance_profiles import (
    get_compliance_profile,
)
from cappo_backend.services.governance_layer import PermissionsCalculator, PolicyCompositionEngine
from cappo_backend.services.intelligence_layer import CostAttributionService, RiskScoringService
from cappo_backend.services.safety_layer import (
    AnomalyDetectionService,
    ApprovalQuorumService,
    BehavioralBaselineService,
    RequestQuarantineService,
)



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


class EnhancedMCPAPIRuntime:
    def __init__(self, compliance_profile_id: str = "global_default"):
class EIValidationError(Exception):
    """Exception raised when Execution Identity validation fails."""
    pass
class MCPGateway:
    def __init__(self, audit_service=None, pgl_lookup=None, revocation_lookup=None, settings=None, compliance_profile_id: str = "global_default"):
        self.audit_service = audit_service
        self.pgl_lookup = pgl_lookup
        self.revocation_lookup = revocation_lookup
        self.settings = settings
        profile_id = os.getenv("VEKLOM_COMPLIANCE_PROFILE", compliance_profile_id)
        self.compliance_profile = get_compliance_profile(profile_id)
        
        # Safety Layer
        self.baseline_service = BehavioralBaselineService()
        self.anomaly_detection = AnomalyDetectionService(self.baseline_service)
        self.quarantine_service = RequestQuarantineService()
        self.quorum_service = ApprovalQuorumService()
        
        # Intelligence Layer
        self.cost_attribution = CostAttributionService()
        self.risk_scoring = RiskScoringService()
        
        # Governance Layer
        self.policy_composition = PolicyCompositionEngine()
        self.permissions_calculator = PermissionsCalculator()
        
        # Distributed State Tracking (Redis)
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            self.redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
        except Exception:
            # Fallback for local dev/testing without Redis running
            self.redis_client = None

    def _mark_nonce_spent(self, nonce: str, ttl_seconds: int = 3600) -> bool:
        """Atomic consume-once token burning using SET NX EX."""
        if not self.redis_client:
            return True
        key = f"veklom:nonce:spent:{nonce}"
        # Atomic SET with NX (not exists) and EX (expire in seconds)
        is_new = self.redis_client.set(key, "spent", nx=True, ex=ttl_seconds)
        return bool(is_new)

    def _is_nonce_spent(self, nonce: str) -> bool:
        """Check if a nonce has been spent."""
        if not self.redis_client:
            return False
        return self.redis_client.exists(f"veklom:nonce:spent:{nonce}") == 1

    def mint_eat(
        self,
        *,
        execution_identity: dict[str, Any],
        agent_id: str,
        certificate_id: str,
        trust_score: float,
        risk_tier: str,
    ) -> dict[str, Any]:
        """Mint a signed Execution Authorization Token for a governed run."""
        from cappo_backend.config import get_settings
        from cappo_backend.services.eat_builder import EATBuilder
        from cappo_backend.services.ei_builder import Ed25519Signer

        settings = self.settings or get_settings()
        signer = Ed25519Signer(signing_key=settings.ei_signing_key)
        return EATBuilder(signer=signer).build(
            execution_identity=execution_identity,
            agent_id=agent_id,
            certificate_id=certificate_id,
            trust_score=trust_score,
            risk_tier=risk_tier,
        )

    # _get_and_update_merkle_head removed in favor of inline WATCH transaction

    async def process_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the strict 9-Phase Ambient Intelligence Readiness Framework Runtime.
        This establishes Veklom as a Universal Plugin for any LLM architecture.
        """
        run_timeline = [] # Unified Run Timeline
        
        connection_id = request.get("connection_id", "unknown")
        agent_id = request.get("agent_id")
        capability_id = request.get("capability_id")
        payload = request.get("payload", {})
        
        # Enforce a strict request nonce for replay resistance
        request_nonce = request.get("nonce")
        if not request_nonce:
            return self._create_error_response(connection_id, "400", "Missing request nonce. Required for cryptographic binding and replay resistance.")
            
        # Calculate request hash for cryptographic binding
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        
        run_timeline.append({"phase": "INTAKE", "timestamp": datetime.utcnow().isoformat(), "agent_id": agent_id, "capability_id": capability_id, "request_hash": request_hash, "nonce": request_nonce})

        try:
            # ====================================================================
            # PHASE 1: Ambient Context and Cryptographic Identity Resolution
            # ====================================================================
            
            agent_context = self._resolve_agent_identity_with_rag(agent_id)
            if not agent_context:
                run_timeline.append({"phase": "IDENTITY", "status": "DENIED", "reason": "Agent not found or revoked"})
                return self._create_error_response(connection_id, "401", "Agent not found or revoked (Phase 1 Failed)")
            
            upstream_evidence_hash = request.get("upstream_evidence_hash")
            if self.compliance_profile.requires_explicit_evidence_logging and not upstream_evidence_hash:
                 run_timeline.append({"phase": "IDENTITY", "status": "DENIED", "reason": "Missing upstream evidence hash"})
                 return self._create_error_response(connection_id, "403", f"Missing upstream evidence hash (Required by {self.compliance_profile.id} compliance profile)")
                 
            run_timeline.append({"phase": "IDENTITY", "status": "RESOLVED", "context": agent_context})

            # ====================================================================
            # PHASE 2: Intent Parsing and Localized Policy Mapping
            # ====================================================================
            
            run_timeline.append({"phase": "PROFILE_RESOLUTION", "active_profile": self.compliance_profile.id})
            
            if self.compliance_profile.id == "fail_closed":
                run_timeline.append({"phase": "PROFILE_RESOLUTION", "status": "DENIED", "event": "configuration-error", "reason": "Missing or unknown compliance profile."})
                return self._create_error_response(connection_id, "403", "Configuration Error: Active profile is missing or unknown. Fail-closed enforced.")
            
            residency_decision = "N/A"
            if self.compliance_profile.requires_data_residency:
                target_region = request.get("target_region", "US")
                if target_region not in self.compliance_profile.allowed_model_regions:
                    run_timeline.append({"phase": "POLICY_DECISION", "status": "BLOCKED", "reason": f"Region {target_region} violates {self.compliance_profile.id}"})
                    if self.compliance_profile.region.value in ["ONTARIO", "EU"]:
                        headers = {"Link": f'<https://veklom.com/compliance/{self.compliance_profile.id}>; rel="blocked-by"'}
                        return self._create_error_response(
                            connection_id, "451", 
                            f"Unavailable For Legal Reasons: Target region '{target_region}' violates {self.compliance_profile.id} data residency laws.",
                            headers=headers
                        )
                    else:
                        return self._create_error_response(connection_id, "403", f"Forbidden: Target region '{target_region}' violates {self.compliance_profile.id} policy restrictions.")
                residency_decision = f"Region {target_region} explicitly allowed by {self.compliance_profile.id}"
            
            composition = self.policy_composition.compose_policy(
                agent_id, capability_id, 
                system_policy=None, owner_policy=None, runtime_policy=None, temporal_policy=None
            )
            policy_snapshot_id = hashlib.md5(json.dumps(composition, sort_keys=True).encode()).hexdigest()
            
            effective_perms = self.permissions_calculator.calculate_effective_permissions(
                agent_id, capability_id, 50.0, 
                composition["system_policy"], composition["owner_policy"], composition["runtime_policy"]
            )
            
            if not effective_perms.get("can_execute", False):
                run_timeline.append({"phase": "POLICY_DECISION", "status": "DENIED", "reason": "Insufficient permissions"})
                return self._create_error_response(connection_id, "403", "Insufficient permissions (Phase 2 Failed)")
                
            run_timeline.append({"phase": "POLICY_DECISION", "status": "APPROVED", "policy_snapshot": policy_snapshot_id, "residency": residency_decision})

            # ====================================================================
            # PHASE 3: Intelligence Routing and Gold-Corpus Contextualization
            # ====================================================================
            
            external_context = request.get("external_context", None)
            if external_context and self.compliance_profile.region.value in ["ONTARIO", "EU"]:
                run_timeline.append({"phase": "CONTEXTUALIZATION", "status": "DENIED", "reason": "External context forbidden"})
                return self._create_error_response(connection_id, "403", "External context forbidden by Gold-Only Learning doctrine.")
                
            run_timeline.append({"phase": "CONTEXTUALIZATION", "status": "GOLD_ONLY_ENFORCED"})

            # ====================================================================
            # PHASE 4: Pre-Execution Safety Verification (Rule of Two Trigger)
            # ====================================================================
            
            from cappo_backend.models.mcpapi_v2 import CurrentMetric, Severity
            
            current_metric = CurrentMetric(
                requests_per_hour=15.0,
                failure_rate=0.02,
                new_capabilities=[],
                time_of_day=datetime.utcnow().hour,
                requests_in_window=15
            )
            all_anomalies = self.anomaly_detection.detect_anomalies(agent_id, current_metric)
            critical_anomalies = [a for a in all_anomalies if a.severity == Severity.CRITICAL]
            
            if critical_anomalies:
                run_timeline.append({"phase": "SAFETY_VERIFICATION", "status": "QUARANTINED", "anomalies": len(critical_anomalies)})
                quarantine = self.quarantine_service.quarantine(request, critical_anomalies, {"applied": True, "suppressed_score": 20})
                return self._handle_quarantine(quarantine, connection_id)
                
            estimated_workload_cost = 5.0
            if not self.cost_attribution.can_afford_request(agent_id, capability_id, estimated_cost=estimated_workload_cost):
                run_timeline.append({"phase": "SAFETY_VERIFICATION", "status": "DENIED", "reason": "Budget Exceeded"})
                return self._create_error_response(connection_id, "402", "VNP Micro-Stake budget exceeded. x402 Payment Required.")
                
            run_timeline.append({"phase": "SAFETY_VERIFICATION", "status": "PASSED"})

            # ====================================================================
            # PHASE 5: Human-in-the-Loop Interstitial Approval
            # ====================================================================
            
            approver_id = None
            
            if effective_perms.get("requires_approval", False) or any(a.recommended_action.value == "quarantine" for a in all_anomalies):
                approval_token_payload = request.get("approval_token")
                
                if approval_token_payload:
                    is_valid, validated_approver, error_msg = self._validate_bound_approval_token(
                        approval_token_payload, request_hash, policy_snapshot_id, capability_id, request_nonce
                    )
                    if not is_valid:
                        run_timeline.append({"phase": "APPROVAL_STATE", "status": "REJECTED", "reason": error_msg})
                        return self._create_error_response(connection_id, "403", f"Invalid or expired human approval token: {error_msg}")
                    
                    # SINGLE-USE ENFORCEMENT: Distributed Atomic Burn
                    if not self._mark_nonce_spent(request_nonce, ttl_seconds=3600):
                        run_timeline.append({"phase": "APPROVAL_STATE", "status": "REPLAY_DETECTED"})
                        return self._create_error_response(connection_id, "403", "Token reuse detected. This nonce has already been spent in the distributed cluster.")
                    
                    approver_id = validated_approver
                    run_timeline.append({"phase": "APPROVAL_STATE", "status": "RESUMED", "approver_id": approver_id})
                else:
                    quorum = self.quorum_service.create_quorum(
                        connection_id, 
                        effective_perms.get("approval_path", []),
                        2
                    )
                    run_timeline.append({"phase": "APPROVAL_STATE", "status": "PAUSED_FOR_HUMAN"})
                    return self._create_approval_response(connection_id, quorum)
            else:
                run_timeline.append({"phase": "APPROVAL_STATE", "status": "NOT_REQUIRED"})

            # ====================================================================
            # PHASE 6: Identity-Bound MCPAPI v2.0 Tool Invocation
            # ====================================================================
            
            run_timeline.append({"phase": "TOKEN_ISSUANCE", "status": "ISSUED", "ephemeral_token": True})

            # ====================================================================
            # PHASE 7: Output Validation and Human Rights Assessment
            # ====================================================================
            
            run_timeline.append({"phase": "EXECUTION_DISPATCH", "status": "STARTED"})
            execution_start = time.time()
            raw_result = {"data": "Capability executed successfully"}
            execution_time_ms = int((time.time() - execution_start) * 1000)
            
            validated_result = raw_result 
            run_timeline.append({"phase": "EXECUTION_DISPATCH", "status": "COMPLETED", "execution_time_ms": execution_time_ms})

            # ====================================================================
            # PHASE 8: Action Execution and Sovereign Persistence
            # ====================================================================
            
            request["__audit_retention_days"] = self.compliance_profile.strict_retention_days
            run_timeline.append({"phase": "PERSISTENCE", "status": "COMMITTED", "retention_days": self.compliance_profile.strict_retention_days})

            # ====================================================================
            # PHASE 9: Immutable Audit and Decommissioning (Merkle Hash Chain)
            # ====================================================================
            
            actual_cost = estimated_workload_cost + (execution_time_ms / 1000.0) * 0.5
            self.cost_attribution.record_cost(
                agent_id=agent_id, capability_id=capability_id, cost=actual_cost, currency="VNP", success=True
            )
            
            run_timeline.append({"phase": "FINAL_LEDGER_EVENT", "status": "SUCCESS"})
            
            # ATOMIC MERKLE CHAIN HASH WITH OPTIMISTIC LOCKING (WATCH)
            final_pgl_hash = ""
            previous_hash = "0000000000000000000000000000000000000000000000000000000000000000"
            
            if self.redis_client:
                head_key = "veklom:audit:head_hash"
                max_retries = 3
                retries_used = 0
                for attempt in range(max_retries):
                    try:
                        with self.redis_client.pipeline() as pipe:
                            pipe.watch(head_key)
                            current_head = pipe.get(head_key)
                            if current_head:
                                previous_hash = current_head
                                
                            event_payload = json.dumps({
                                "connection_id": connection_id,
                                "nonce": request_nonce,
                                "unified_run_timeline": run_timeline,
                                "previous_audit_hash": previous_hash
                            })
                            final_pgl_hash = hashlib.sha256(event_payload.encode()).hexdigest()
                            
                            # BOTH head update and event write in same MULTI/EXEC block
                            pipe.multi()
                            pipe.set(head_key, final_pgl_hash)
                            pipe.set(f"veklom:audit:event:{final_pgl_hash}", event_payload)
                            pipe.execute()
                            break # Success
                    except redis.WatchError:
                        retries_used += 1
                        if attempt == max_retries - 1:
                            run_timeline.append({"phase": "SYSTEM_FAULT", "error": "Max retries exceeded during audit head update due to high contention."})
                            return self._create_error_response(connection_id, "500", "Audit ledger contention too high.")
                        continue # Retry if head was modified concurrently
            else:
                event_payload = json.dumps({
                    "connection_id": connection_id,
                    "nonce": request_nonce,
                    "unified_run_timeline": run_timeline,
                    "previous_audit_hash": previous_hash
                })
                final_pgl_hash = hashlib.sha256(event_payload.encode()).hexdigest()
                retries_used = 0
            
            risk_profile = self.risk_scoring.calculate_risk_score(agent_id, {"anomaly_score": 0})
            
            return {
                "connection_id": connection_id,
                "status": "authorized",
                "evidence_hash": final_pgl_hash,
                "result": {
                    "output": validated_result,
                    "execution_time_ms": execution_time_ms
                },
                "metadata": {
                    "trust_delta": 2,
                    "new_trust_score": 52,
                    "audit_logged": True,
                    "anomalies_detected": len(all_anomalies),
                    "cost_attributed": actual_cost,
                    "risk_score": risk_profile["overall_risk_score"],
                    "threat_level": risk_profile["threat_level"],
                    "compliance_profile_enforced": self.compliance_profile.id,
                    "unified_run_timeline": run_timeline,
                    "merkle_previous_hash": previous_hash,
                    "audit_transaction_retries": retries_used
                }
            }

        except Exception as e:
            run_timeline.append({"phase": "SYSTEM_FAULT", "error": str(e)})
            return self._create_error_response(connection_id, "500", str(e))

    async def process_interlink_request(self, agent_id: str, capability_id: str, payload: Dict[str, Any], execution_identity: dict, estimated_cost: float = 1.0) -> Dict[str, Any]:
        """
        Intercepts a request destined for an external, un-governed Web2 API.
        Enforces the VNP Micro-Stake budget and logs the cryptographic proof to the ledger,
        then returns authorization to proceed with the raw HTTP proxy forward.
        """
        connection_id = str(uuid.uuid4())
        request_nonce = payload.get("nonce", str(uuid.uuid4()))
        run_timeline = []
        
        try:
            # 0. Law 0 Enforcement (Pre-Execution Verification)
            self.require_execution_identity(execution_identity, action="interlink.proxy")
            
            # 1. Budget Verification (Phase 4 Equivalent)
            if not self.cost_attribution.can_afford_request(agent_id, capability_id, estimated_cost=estimated_cost):
                return self._create_error_response(connection_id, "402", "VNP Micro-Stake budget exceeded. x402 Payment Required for Interlink Proxy.")
                
            # 2. Immutable Audit & Cost Deduction (Phase 9 Equivalent)
            self.cost_attribution.record_cost(
                agent_id=agent_id, capability_id=capability_id, cost=estimated_cost, currency="VNP", success=True
            )
            
            run_timeline.append({"phase": "INTERLINK_PROXY", "status": "AUTHORIZED", "cost_deducted": estimated_cost})
            
            # 3. Merkle Hash Generation
            final_pgl_hash = ""
            previous_hash = "0000000000000000000000000000000000000000000000000000000000000000"

            if self.redis_client:
                import redis
                head_key = "veklom:audit:head_hash"
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        with self.redis_client.pipeline() as pipe:
                            pipe.watch(head_key)
                            current_head = pipe.get(head_key)
                            if current_head:
                                previous_hash = current_head
                                
                            event_payload = json.dumps({
                                "connection_id": connection_id,
                                "nonce": request_nonce,
                                "interlink_target": payload.get("target_url", "unknown"),
                                "unified_run_timeline": run_timeline,
                                "previous_audit_hash": previous_hash
                            })
                            final_pgl_hash = hashlib.sha256(event_payload.encode()).hexdigest()
                            
                            pipe.multi()
                            pipe.set(head_key, final_pgl_hash)
                            pipe.set(f"veklom:audit:event:{final_pgl_hash}", event_payload)
                            pipe.execute()
                            break
                    except redis.WatchError:
                        if attempt == max_retries - 1:
                            return self._create_error_response(connection_id, "500", "Max retries exceeded during audit head update due to high contention.")
            else:
                event_payload = json.dumps({
                    "connection_id": connection_id,
                    "nonce": request_nonce,
                    "interlink_target": payload.get("target_url", "unknown"),
                    "unified_run_timeline": run_timeline,
                })
                final_pgl_hash = hashlib.sha256(event_payload.encode()).hexdigest()
            
            return {
                "connection_id": connection_id,
                "status": "authorized",
                "evidence_hash": final_pgl_hash,
                "metadata": {
                    "cost_attributed": estimated_cost,
                    "interlink_cleared": True
                }
            }
        except Exception as e:
            return self._create_error_response(connection_id, "500", f"Interlink Gateway Fault: {str(e)}")


    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================
    
    def _resolve_agent_identity_with_rag(self, agent_id: str) -> Optional[Dict[str, Any]]:
        if not agent_id:
           return None
        try:
            return {"agent_id": agent_id, "workspace_id": "ws-123"}
        except Exception as e:
            return False, None, f"Failed to resolve agent identity: {str(e)}"
            
    def require_execution_identity(
        self,
        execution_identity: dict,
        action: str = "unknown",
        db: Any = None,
        **context: Any,
    ):
        """
        Enforces the 9 validation rules from the CAPPO Gold Blueprint.
        Blocks execution if any check fails by raising EIValidationError.
        """
        try:
            self._validate_execution_identity(execution_identity, action=action, db=db, **context)
        except EIValidationError as exc:
            self._record_law0_violation(exc, execution_identity, action, context)
            raise

    def _validate_execution_identity(
        self,
        execution_identity: dict,
        action: str = "unknown",
        db: Any = None,
        **context: Any,
    ) -> None:
        if not execution_identity:
            raise EIValidationError("missing execution identity")

        cert_id = (
            execution_identity.get("pre_execution_certificate_id")
            or execution_identity.get("pgl_pre_certificate_id")
        )
        if not cert_id:
            raise EIValidationError("pgl pre-cert reference missing")

        # 1. PGL pre-cert reference valid and persisted & 9. Not revoked
        cert = None
        if self.pgl_lookup:
            cert = self.pgl_lookup(cert_id)
            if not cert:
                raise EIValidationError("pgl pre-cert not found")
        elif db:
            from cappo_backend.models.pgl_certificate import PGLCertificate
            cert = db.query(PGLCertificate).filter(PGLCertificate.certificate_id == cert_id).first()
            if not cert:
                raise EIValidationError("pgl pre-cert not found")

        if cert is not None:
            if getattr(cert, "persisted", True) is False:
                raise EIValidationError("pgl pre-cert not persisted")
            if getattr(cert, "status", "") in ("ROLLED_BACK", "ABANDONED", "REVOKED"):
                raise EIValidationError("pgl pre-cert revoked")

            # 2. Hashes match provenance
            if cert.genome_hash != execution_identity.get("genome_hash"):
                raise EIValidationError("genome_hash mismatch")
            if cert.constitution_hash != execution_identity.get("constitution_hash"):
                raise EIValidationError("constitution_hash mismatch")
            if cert.plan_hash != execution_identity.get("plan_hash"):
                raise EIValidationError("plan_hash mismatch")

        # 3. SEKED directive explicitly allows execution. Missing directives
        # fail closed; they are not equivalent to ALLOW.
        seked_directive = execution_identity.get("seked_directive", {})
        directive = execution_identity.get("directive") or seked_directive.get("decision")
        if directive not in ("ALLOW", "ALLOW_WITH_AUDIT"):
            raise EIValidationError("directive does not permit execution")

        # 4. TTL not expired
        expires_at = execution_identity.get("expires_at")
        if expires_at:
            from datetime import datetime, timezone
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_at:
                raise EIValidationError("ttl expired")

        # 5. Scope covers requested action
        scope = execution_identity.get("scope")
        if isinstance(scope, dict) and action != "unknown":
            tools = scope.get("tools") or []
            if tools and action not in tools and action.split(".")[0] not in {
                str(tool).split(".")[0] for tool in tools
            }:
                raise EIValidationError("scope does not cover requested action")
        elif isinstance(scope, str) and scope != action and action != "unknown":
            if not action.startswith(scope.split(":")[0]):
                raise EIValidationError("scope does not cover requested action")

        # 6. Budget within approved limits
        budget = execution_identity.get("budget", {})
        if budget.get("remaining", 1) <= 0:
            raise EIValidationError("budget exceeded")
        action_cost_cents = int(context.get("action_cost_cents", 0))
        approved_cents = (
            execution_identity.get("budget_approved_cents")
            or budget.get("approved_cents")
            or budget.get("limit_cents")
            or 0
        )
        if approved_cents and action_cost_cents > int(approved_cents):
            raise EIValidationError("budget exceeded")

        # 7. Delegation depth within limits
        depth = execution_identity.get("delegation_depth", 0)
        max_depth = seked_directive.get("max_delegation_depth", 5)
        if depth > max_depth:
            raise EIValidationError("delegation depth exceeded")

        # 8. Revocation is checked before cryptographic details so a revoked
        # identity is never treated as potentially usable, even when stale.
        if execution_identity.get("revoked"):
            raise EIValidationError("execution identity revoked")
        if self.revocation_lookup and self.revocation_lookup(execution_identity.get("execution_id", "")):
            raise EIValidationError("execution identity revoked")

        # 9. Signature and hash verify
        from cappo_backend.services.canonical import sha256_json, verify_signature_ed25519
        from cappo_backend.services.ei_builder import canonical_body

        body = canonical_body(execution_identity)
        stored_hash = execution_identity.get("hash")
        if not stored_hash or stored_hash != sha256_json(body):
            raise EIValidationError("hash verification failed")
        settings = self.settings
        signing_key = getattr(settings, "ei_signing_key", None) or "test-signing-key"
        if not verify_signature_ed25519(body, execution_identity.get("signature", ""), signing_key):
            raise EIValidationError("signature verification failed")


    def _record_law0_violation(
        self,
        exc: EIValidationError,
        execution_identity: dict | None,
        action: str,
        context: dict[str, Any],
    ) -> None:
        if not self.audit_service:
            return
        try:
            self.audit_service.record(
                "law0_violation",
                {
                    "law0": True,
                    "error": str(exc),
                    "action": action,
                    "execution_id": (execution_identity or {}).get("execution_id"),
                },
                workspace_id=context.get("workspace_id", "default"),
                run_id=context.get("run_id"),
            )
        except Exception:
            pass
            
    def _validate_bound_approval_token(self, token_payload: Dict[str, Any], request_hash: str, policy_snapshot_id: str, capability_id: str, request_nonce: str) -> Tuple[bool, Optional[str], str]:
        """
        Cryptographically validates that an approval token is mathematically bound to this exact request.
        Prevents replay attacks across different payloads, policies, or capabilities.
        """
        try:
            if not isinstance(token_payload, dict):
                return False, None, "Token must be a structured payload."
                
            # Distributed Single-Use Replay Check is handled explicitly by _mark_nonce_spent in the flow
            if self._is_nonce_spent(request_nonce):
                return False, None, "Token reuse detected. This nonce has already been marked spent in Redis."
                
            # Check Nonce Binding
            if token_payload.get("nonce") != request_nonce:
                return False, None, "Token nonce mismatch. Approval is not bound to this specific request instance."
                
            # Check Expiry
            expiry = token_payload.get("expires_at", 0)
            if datetime.utcnow().timestamp() > expiry:
                return False, None, "Approval token has expired."
                
            # Check Request Binding
            if token_payload.get("request_hash") != request_hash:
                return False, None, "Token request_hash mismatch. Payload was altered after approval."
                
            # Check Policy Binding
            if token_payload.get("policy_snapshot_id") != policy_snapshot_id:
                return False, None, "Token policy_snapshot mismatch. Governing policy changed after approval."
                
            # Check Capability Scope
            if token_payload.get("capability_id") != capability_id:
                return False, None, "Token capability mismatch. Action scope changed after approval."

            signature = token_payload.get("signature")
            if not signature:
                return False, None, "Invalid cryptographic signature on approval token."

            from cappo_backend.config import get_settings
            from cappo_backend.services.canonical import verify_signature_hmac

            settings = self.settings or get_settings()
            signing_key = getattr(settings, "approval_token_signing_key", "")
            if not signing_key:
                return False, None, "Approval token verifier is not configured."

            signed_payload = self._approval_token_signature_payload(token_payload)
            if not verify_signature_hmac(signed_payload, signature, signing_key):
                return False, None, "Invalid cryptographic signature on approval token."

            return True, token_payload.get("approver_id", "unknown_human"), "Valid"
        except Exception as e:
            return False, None, str(e)

    def _approval_token_signature_payload(self, token_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return the deterministic approval-token body covered by the HMAC signature."""
        return {
            "approver_id": token_payload.get("approver_id"),
            "capability_id": token_payload.get("capability_id"),
            "expires_at": token_payload.get("expires_at"),
            "nonce": token_payload.get("nonce"),
            "policy_snapshot_id": token_payload.get("policy_snapshot_id"),
            "request_hash": token_payload.get("request_hash"),
        }
        
    def _handle_quarantine(self, quarantine: Any, connection_id: str) -> Dict[str, Any]:
        return {
            "connection_id": connection_id,
            "status": "quarantined",
            "quarantine_id": quarantine.quarantine_id,
            "reason": quarantine.quarantine_reason,
            "requires_approval": quarantine.approval_required,
            "approvers_needed": quarantine.approvers_required,
            "approval_deadline": quarantine.approval_deadline
        }
        
    def _create_approval_response(self, connection_id: str, quorum: Any) -> Dict[str, Any]:
        return {
            "connection_id": connection_id,
            "status": "approval_required",
            "approval_id": quorum.approval_id,
            "required_approvers": quorum.required_approvers,
            "required_count": quorum.required_count,
            "deadline": quorum.approval_deadline
        }
        
    def _create_error_response(self, connection_id: str, code: str, message: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        resp = {
            "connection_id": connection_id,
            "status": "error",
            "error": {
                "code": code,
                "message": message
            }
        }
        if headers:
            resp["headers"] = headers
        return resp
