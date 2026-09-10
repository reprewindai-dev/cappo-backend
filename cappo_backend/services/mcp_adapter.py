"""Cappy MCP v2 Semantic Adapter — Bridges MCP v2 wire protocol with Veklom CAPPO governance.

Follows the canonical decoupling pattern from `pgl_adapter.py`.
Normalizes MCP v2 JSON-RPC `tools/call` requests into Veklom semantic models:
- `ConnectionContext`
- `CapabilityContext`
- `AuthorityBundle`, `VeklomAgent`, `AuthorityPermissions` from `mcpapi_v2.py`

Enforces:
1. Perimeter anti-replay: Atomic single-use nonce validation before CAPPO is reached.
2. Perimeter session validation: Rejects fabricated/invalid MCP sessions before CAPPO is reached.
3. The 8-way authority intersection invariant:
   authority = identity ∩ connection ∩ capability ∩ operation
             ∩ execution identity ∩ policy ∩ lifecycle
             ∩ current CAPPO authorization
4. Invariant: `MCP session != authority; CAPPO decides`.
5. Consequence Dominance: Consequence execution occurs ONLY if CAPPO grants ALLOW.
   On ALLOW: executes consequence and emits PGL receipt with Merkle evidence hash.
   On DENY: halts immediately, consequence does not execute, no receipt emitted.

Infrastructure Hard Lock:
Target stack is Docker + Cloudflare Tunnels + self-hosted hardware ONLY.
Zero Vercel dependencies, configs, or edge runtime assumptions.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from pydantic import BaseModel, Field

from cappo_backend.config import Settings, get_settings
from cappo_backend.models.mcpapi_v2 import (
    AuthorityBundle,
    AuthorityPermissions,
    GovernanceTier,
    VeklomAgent,
)
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.mcp_v2 import MCPv2Stack, get_mcp_v2_stack
from cappo_backend.services.safety import CurrentMetric

logger = logging.getLogger(__name__)

# Maximum MCP payload limits to prevent unbounded operations
MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KiB
MAX_ARGUMENT_KEYS = 64

DENIAL_STRINGS = frozenset({
    "false",
    "deny",
    "0",
    "block",
    "reject",
    "hold",
    "revoke",
    "quarantine",
    "none",
})

ALLOWED_STRINGS = frozenset({
    "true",
    "allow",
})


def _parse_canonical_boolean(val: Any, *, default: bool = False) -> bool:
    """Strict fail-closed boolean resolver for wire/serialized governance decisions."""
    if val is None:
        return default
    if val is True:
        return True
    if val is False:
        return False
    if isinstance(val, str):
        cleaned = val.strip().lower()
        if cleaned in DENIAL_STRINGS:
            return False
        if cleaned in ALLOWED_STRINGS:
            return True
        return False
    return False


# ============================================================================
# CONTEXT MODELS (CAPPY SEMANTIC COMPILER)
# ============================================================================

class ConnectionContext(BaseModel):
    """Normalized connection aggregate for governed execution."""
    connection_id: str
    actor_id: str
    transport: str = "mcp"
    protocol_version: str = "2.0"
    intent: str
    idempotency_key: str
    session_id: Optional[str] = None
    workspace_id: Optional[str] = None
    deadline_at: Optional[str] = None
    evidence_mode: str = "consequential"
    status: str = "active"


class CapabilityContext(BaseModel):
    """Normalized capability context for consequence evaluation."""
    tenant_id: str
    actor_id: str
    scopes: List[str] = Field(default_factory=list)
    risk_tier: str = "standard"
    capability_id: str = "exec"
    action: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    environment: str = "production"
    untrusted_content_state: str = "clean"
    approval_state: str = "none"
    nonce: Optional[str] = None
    operation_id: Optional[str] = None
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class IntersectionResult:
    """Detailed evaluation of the 8-way authority intersection invariant.

    authority = identity ∩ connection ∩ capability ∩ operation
              ∩ execution identity ∩ policy ∩ lifecycle
              ∩ current CAPPO authorization
    """
    identity_valid: bool
    connection_valid: bool
    capability_valid: bool
    operation_valid: bool
    execution_identity_valid: bool
    policy_valid: bool
    lifecycle_valid: bool
    cappo_authorized: bool
    effective_authority: bool
    failure_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity_valid,
            "connection": self.connection_valid,
            "capability": self.capability_valid,
            "operation": self.operation_valid,
            "execution_identity": self.execution_identity_valid,
            "policy": self.policy_valid,
            "lifecycle": self.lifecycle_valid,
            "cappo_authorization": self.cappo_authorized,
            "effective_authority": self.effective_authority,
            "failure_reasons": self.failure_reasons,
        }


# ============================================================================
# EXCEPTIONS
# ============================================================================

class PerimeterDefenseError(Exception):
    """Base error for perimeter check failures before CAPPO evaluation."""
    def __init__(self, code: str, message: str, rpc_code: int = -32000) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.rpc_code = rpc_code


class ReplayAttackError(PerimeterDefenseError):
    """Raised when an execution nonce is reused or replayed."""
    def __init__(self, message: str = "Token reuse detected: nonce already spent") -> None:
        super().__init__(code="LAW0_REPLAY_DETECTED", message=message, rpc_code=-32003)


class InvalidSessionError(PerimeterDefenseError):
    """Raised when an MCP session is fabricated, unknown, or expired."""
    def __init__(self, message: str = "Session invalid, fabricated, or expired") -> None:
        super().__init__(code="INVALID_MCP_SESSION", message=message, rpc_code=-32001)


class PayloadBoundExceededError(PerimeterDefenseError):
    """Raised when tool arguments exceed safety bounds."""
    def __init__(self, message: str = "Payload exceeds size or field limits") -> None:
        super().__init__(code="PAYLOAD_BOUND_EXCEEDED", message=message, rpc_code=-32602)


class GovernanceDeniedError(Exception):
    """Raised when CAPPO or the 8-way intersection denies consequence execution."""
    def __init__(
        self,
        reason: str,
        intersection: IntersectionResult | None = None,
        evidence: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.intersection = intersection
        self.evidence = evidence


# ============================================================================
# PORT PROTOCOL (STRUCTURAL INTERFACE)
# ============================================================================

@runtime_checkable
class MCPPort(Protocol):
    """Unified structural interface for MCP v2 protocol operations.

    Satisfied by MCPv2Adapter, following the decoupling pattern of PGLPort in pgl_adapter.py.
    """

    def normalize_request(
        self,
        raw_request: Dict[str, Any],
        headers: Dict[str, str] | None = None,
    ) -> Tuple[ConnectionContext, CapabilityContext, VeklomAgent, AuthorityBundle]:
        """Normalize an inbound MCP JSON-RPC tools/call wire request into Veklom semantic models."""
        ...

    def assess_consequence_eligibility(
        self,
        agent_id: str,
        capability_id: str,
        request: Dict[str, Any],
        *,
        metric: CurrentMetric | None = None,
        trust_score: float = 100.0,
        system_policy: Any | None = None,
        owner_policy: Any | None = None,
        runtime_policy: Any | None = None,
        at: datetime | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Evaluate pre-execution governance phases via CAPPO MCPv2Stack."""
        ...

    def dispatch_execution(
        self,
        connection_ctx: ConnectionContext,
        capability_ctx: CapabilityContext,
        execution_handler: Callable[..., Any],
        *,
        assessment: Dict[str, Any] | None = None,
        session_auth: Dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Enforce the 8-way intersection invariant and dispatch consequence execution only if authorized."""
        ...

    def format_response(
        self,
        execution_result: Any,
        connection_id: str,
        *,
        request_id: str | int | None = None,
        evidence_hash: str | None = None,
        receipt: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Format a successful JSON-RPC 2.0 response."""
        ...

    def format_error(
        self,
        code: int | str,
        message: str,
        connection_id: str | None = None,
        *,
        request_id: str | int | None = None,
        data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Format a structured JSON-RPC 2.0 error response."""
        ...

    def handle_tool_call(
        self,
        raw_request: Dict[str, Any],
        headers: Dict[str, str] | None = None,
        execution_handler: Callable[..., Any] | None = None,
        session_auth: Dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Full end-to-end handler coordinating perimeter defense, normalization, assessment, and dispatch."""
        ...


# ============================================================================
# ADAPTER IMPLEMENTATION
# ============================================================================

class MCPv2Adapter:
    """Cappy MCP v2 Semantic Adapter satisfying MCPPort.

    Translates between MCP v2 wire format (`tools/call`) and CAPPO consequence
    governance. Guarantees that an MCP session does not equal authority, and
    that consequence execution is dominated by CAPPO's 8-way intersection check.
    """

    def __init__(
        self,
        stack: MCPv2Stack | None = None,
        db: Any | None = None,
        pgl_client: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._stack = stack or get_mcp_v2_stack()
        self._db = db
        self._pgl_client = pgl_client
        self._settings = settings or get_settings()
        self._spent_nonces: set[str] = set()
        self._nonce_lock = threading.Lock()
        self._merkle_counter = 0
        self._merkle_lock = threading.Lock()

    # ------------------------------------------------------------------------
    # Perimeter Defense (Single-Use Nonce & Session Check)
    # ------------------------------------------------------------------------

    def validate_perimeter(
        self,
        headers: Dict[str, str] | None,
        raw_request: Dict[str, Any],
        session_auth: Dict[str, Any] | None = None,
    ) -> str:
        """Validate perimeter before CAPPO decision engine is reached.

        Fails deterministically if:
        - Request payload is not a dictionary (-32600).
        - Tool arguments exceed memory or key safety bounds (-32602).
        - Nonce is missing, malformed, or already spent (-32003).
        - Session is missing, unauthenticated, invalid, expired, or fabricated (-32001).

        Returns the validated nonce.
        """
        headers = headers or {}

        # 0. Syntactic dictionary validation (Vulnerability 3 fix)
        if not isinstance(raw_request, dict):
            raise PerimeterDefenseError(
                code="INVALID_JSONRPC_PAYLOAD",
                message="Request payload must be a JSON-RPC 2.0 object",
                rpc_code=-32600,
            )

        raw_params = raw_request.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        raw_meta = params.get("_meta")
        meta = raw_meta if isinstance(raw_meta, dict) else {}

        # 1. Early payload bounds defense (DDoS & memory exhaustion protection)
        arguments = params.get("arguments")
        if isinstance(arguments, dict):
            try:
                serialized_args = json.dumps(arguments)
                if len(serialized_args.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                    raise PayloadBoundExceededError(
                        f"Arguments payload exceeds maximum allowed size ({MAX_PAYLOAD_BYTES} bytes)"
                    )
                if len(arguments) > MAX_ARGUMENT_KEYS:
                    raise PayloadBoundExceededError(
                        f"Arguments key count ({len(arguments)}) exceeds maximum allowed ({MAX_ARGUMENT_KEYS})"
                    )
            except (TypeError, ValueError) as e:
                raise PerimeterDefenseError(
                    code="PAYLOAD_BOUND_EXCEEDED",
                    message=f"Failed to serialize arguments payload: {e}",
                    rpc_code=-32602,
                )

        # 2. Nonce verification & atomic burn (LAW0 Replay Defense)
        nonce = (
            headers.get("X-Nonce")
            or headers.get("x-nonce")
            or meta.get("nonce")
        )
        if not nonce or not isinstance(nonce, str) or len(nonce.strip()) < 16:
            raise PerimeterDefenseError(
                code="LAW0_NONCE_INVALID",
                message="Nonce is missing, not a string, or shorter than 16 characters",
                rpc_code=-32003,
            )

        nonce = nonce.strip().lower()
        with self._nonce_lock:
            if nonce in self._spent_nonces:
                logger.warning("Replay attack detected: nonce '%s' already spent", nonce)
                raise ReplayAttackError(f"Token reuse detected: nonce '{nonce}' has already been spent")
            # Atomically record nonce consumption
            self._spent_nonces.add(nonce)

        # 3. Session verification (Zero-Trust Fail-Closed Session Boundary - Vulnerability 2 fix)
        if session_auth is None:
            raise InvalidSessionError("Authentication context required: session_auth is missing or unauthenticated")

        if not isinstance(session_auth, dict) or not session_auth.get("valid_session", False):
            raise InvalidSessionError("Fabricated or invalid MCP session rejected at perimeter")

        if not session_auth.get("authenticated", False):
            raise InvalidSessionError("MCP session caller is unauthenticated")

        status = session_auth.get("status", "active")
        if status in ("expired", "revoked", "suspended") or status != "active":
            raise InvalidSessionError(f"MCP session is {status}")

        session_id = (
            headers.get("Mcp-Session-Id")
            or headers.get("mcp-session-id")
            or meta.get("session_id")
        )
        if "session_id" in session_auth and session_id:
            expected_sess = session_auth["session_id"]
            if session_id != expected_sess:
                raise InvalidSessionError("Session ID in request does not match authenticated session")

        return nonce

    # ------------------------------------------------------------------------
    # Request Normalization
    # ------------------------------------------------------------------------

    def normalize_request(
        self,
        raw_request: Dict[str, Any],
        headers: Dict[str, str] | None = None,
    ) -> Tuple[ConnectionContext, CapabilityContext, VeklomAgent, AuthorityBundle]:
        """Map wire tools/call JSON-RPC request to Veklom semantic contracts.

        Enforces payload bounds (<= 64 KiB, <= 64 fields).
        """
        headers = headers or {}
        if not isinstance(raw_request, dict):
            raise ValueError("Invalid JSON-RPC request: expected dictionary payload")

        params = raw_request.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Invalid tools/call: 'params' must be an object")

        tool_name = params.get("name")
        if not tool_name or not isinstance(tool_name, str):
            raise ValueError("Invalid tools/call: 'name' is required and must be a string")

        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Invalid tools/call: 'arguments' must be a dictionary")

        # Bounds check on payload
        serialized_args = json.dumps(arguments)
        if len(serialized_args.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise PayloadBoundExceededError(
                f"Arguments payload exceeds maximum allowed size ({MAX_PAYLOAD_BYTES} bytes)"
            )
        if len(arguments) > MAX_ARGUMENT_KEYS:
            raise PayloadBoundExceededError(
                f"Arguments key count ({len(arguments)}) exceeds maximum allowed ({MAX_ARGUMENT_KEYS})"
            )

        meta = params.get("_meta", {}) if isinstance(params.get("_meta"), dict) else {}

        # Connection attributes
        connection_id = (
            headers.get("X-Veklom-Connection-ID")
            or headers.get("x-veklom-connection-id")
            or meta.get("connection_id")
            or f"conn-{uuid.uuid4()}"
        )

        actor_id = (
            headers.get("X-Veklom-Agent-ID")
            or headers.get("x-veklom-agent-id")
            or headers.get("X-Agent-ID")
            or meta.get("agent_id")
            or meta.get("actor_id")
            or "agent-mcp-anonymous"
        )

        session_id = (
            headers.get("Mcp-Session-Id")
            or headers.get("mcp-session-id")
            or meta.get("session_id")
        )

        idempotency_key = (
            headers.get("Idempotency-Key")
            or headers.get("idempotency-key")
            or meta.get("idempotency_key")
            or f"idem-{uuid.uuid4()}"
        )

        intent = meta.get("intent") or f"execute_tool:{tool_name}"
        workspace_id = meta.get("workspace_id") or headers.get("X-Workspace-ID") or "default-workspace"
        tenant_id = meta.get("tenant_id") or headers.get("X-Tenant-ID") or workspace_id
        deadline_at = meta.get("deadline") or meta.get("deadline_at")
        risk_tier = meta.get("risk_tier", "standard")
        scopes = meta.get("scopes") or [tool_name]
        nonce = headers.get("X-Nonce") or headers.get("x-nonce") or meta.get("nonce")

        # Construct ConnectionContext
        conn_ctx = ConnectionContext(
            connection_id=str(connection_id),
            actor_id=str(actor_id),
            transport="mcp",
            protocol_version="2.0",
            intent=str(intent),
            idempotency_key=str(idempotency_key),
            session_id=str(session_id) if session_id else None,
            workspace_id=str(workspace_id),
            deadline_at=str(deadline_at) if deadline_at else None,
            evidence_mode=meta.get("evidence_mode", "consequential"),
            status="active",
        )

        # Construct CapabilityContext
        cap_ctx = CapabilityContext(
            tenant_id=str(tenant_id),
            actor_id=str(actor_id),
            scopes=list(scopes) if isinstance(scopes, list) else [str(scopes)],
            risk_tier=str(risk_tier),
            capability_id=str(tool_name),
            action=str(tool_name),
            payload=arguments,
            environment=meta.get("environment", "production"),
            untrusted_content_state=meta.get("untrusted_content_state", "clean"),
            approval_state=meta.get("approval_state", "none"),
            nonce=str(nonce.strip().lower()) if isinstance(nonce, str) else (str(nonce) if nonce else None),
            operation_id=str(idempotency_key),
            expires_at=str(deadline_at) if deadline_at else None,
        )

        # Construct VeklomAgent model
        veklom_agent = VeklomAgent(
            agent_id=str(actor_id),
            agent_name=f"agent-{actor_id}",
            mission_file="mcp_v2_session",
            authority_bundle_id=f"bundle-{actor_id}",
            owner_id=str(tenant_id),
            created_at=datetime.now(timezone.utc).isoformat(),
            public_key=meta.get("public_key", ""),
            governance_tier=GovernanceTier.SERVICE,
            associated_mcp_servers=["cappo-mcp-v2"],
        )

        # Construct AuthorityPermissions model
        permissions = AuthorityPermissions(
            can_execute=True,
            can_delegate=False,
            can_read_ledger=True,
            can_write_ledger=False,
        )

        # Construct AuthorityBundle model
        auth_bundle = AuthorityBundle(
            bundle_id=f"bundle-{actor_id}",
            agent_id=str(actor_id),
            issued_by="cappo-mcp-adapter",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            mcp_tools=cap_ctx.scopes,
            permissions=permissions,
            signature="",
        )

        return conn_ctx, cap_ctx, veklom_agent, auth_bundle

    # ------------------------------------------------------------------------
    # 8-Way Intersection Invariant Evaluation
    # ------------------------------------------------------------------------

    def evaluate_8way_intersection(
        self,
        connection_ctx: ConnectionContext,
        capability_ctx: CapabilityContext,
        cappo_assessment: Dict[str, Any],
        session_auth: Dict[str, Any] | None = None,
    ) -> IntersectionResult:
        """Evaluate the 8-way intersection invariant:

        authority = identity ∩ connection ∩ capability ∩ operation
                  ∩ execution identity ∩ policy ∩ lifecycle
                  ∩ current CAPPO authorization
        """
        reasons: List[str] = []

        # 1. Identity: Must be authenticated and non-empty (fail-closed)
        caller_authenticated = bool(
            isinstance(session_auth, dict)
            and session_auth.get("authenticated") is True
        )
        identity_valid = bool(
            connection_ctx.actor_id
            and connection_ctx.actor_id != "anonymous"
            and caller_authenticated
        )
        if not identity_valid:
            reasons.append("identity: Actor identity missing or unauthenticated")

        # 2. Connection: Must be valid format and active
        connection_valid = bool(
            connection_ctx.connection_id
            and connection_ctx.status == "active"
        )
        if not connection_valid:
            reasons.append(f"connection: Connection '{connection_ctx.connection_id}' is not active")

        # 3. Capability: Action must be permitted in declared scopes
        capability_valid = bool(
            capability_ctx.capability_id
            and (
                capability_ctx.capability_id in capability_ctx.scopes
                or "*" in capability_ctx.scopes
            )
        )
        if not capability_valid:
            reasons.append(
                f"capability: Capability '{capability_ctx.capability_id}' not covered in scopes {capability_ctx.scopes}"
            )

        # 4. Operation: Arguments payload bounded and valid
        operation_valid = (
            isinstance(capability_ctx.payload, dict)
            and len(capability_ctx.payload) <= MAX_ARGUMENT_KEYS
        )
        if not operation_valid:
            reasons.append("operation: Arguments exceed parameter bounds or invalid structure")

        # 5. Execution Identity: Context bounds unexpired
        ei_valid = True
        if capability_ctx.expires_at:
            try:
                exp = datetime.fromisoformat(capability_ctx.expires_at.replace("Z", "+00:00"))
                if exp < datetime.now(timezone.utc):
                    ei_valid = False
                    reasons.append("execution_identity: Context deadline has expired")
            except Exception:
                pass
        execution_identity_valid = ei_valid

        # 6. Policy: Checked against CAPPO policy output
        gov_info = cappo_assessment.get("governance", {}) if isinstance(cappo_assessment, dict) else {}
        policy_valid = _parse_canonical_boolean(gov_info.get("policy_allows"), default=True)
        if not policy_valid:
            reasons.append("policy: Composed governance policy denies action")

        # 7. Lifecycle: Connection and session must not be terminated/spent (fail-closed)
        session_active = bool(
            isinstance(session_auth, dict)
            and session_auth.get("status", "active") == "active"
        )
        lifecycle_valid = bool(
            connection_ctx.status == "active"
            and session_active
        )
        if not lifecycle_valid:
            reasons.append("lifecycle: Session or connection lifecycle is terminated/suspended")

        # 8. Current CAPPO authorization: Must explicitly evaluate to True
        raw_allow = cappo_assessment.get("allow") if isinstance(cappo_assessment, dict) else None
        cappo_authorized = _parse_canonical_boolean(raw_allow, default=False)

        # Cross-layer enforcement: override authorization if safety or decision directives deny
        if isinstance(cappo_assessment, dict):
            safety = cappo_assessment.get("safety")
            if isinstance(safety, dict):
                safety_action = str(safety.get("recommended_action", "")).strip().lower()
                if safety_action in ("block", "quarantine") or safety.get("quarantine_id"):
                    cappo_authorized = False

            top_decision = str(
                cappo_assessment.get("decision") or cappo_assessment.get("directive") or ""
            ).strip().lower()
            if top_decision in DENIAL_STRINGS:
                cappo_authorized = False

        if not cappo_authorized:
            safety_action = (
                cappo_assessment.get("safety", {}).get("recommended_action", "block")
                if isinstance(cappo_assessment, dict) and isinstance(cappo_assessment.get("safety"), dict)
                else "block"
            )
            reasons.append(
                f"cappo_authorization: CAPPO pre-execution assessment returned allow=False (action: {safety_action})"
            )

        # Effective authority is the non-empty intersection of all 8 components
        effective_authority = (
            identity_valid
            and connection_valid
            and capability_valid
            and operation_valid
            and execution_identity_valid
            and policy_valid
            and lifecycle_valid
            and cappo_authorized
        )

        return IntersectionResult(
            identity_valid=identity_valid,
            connection_valid=connection_valid,
            capability_valid=capability_valid,
            operation_valid=operation_valid,
            execution_identity_valid=execution_identity_valid,
            policy_valid=policy_valid,
            lifecycle_valid=lifecycle_valid,
            cappo_authorized=cappo_authorized,
            effective_authority=effective_authority,
            failure_reasons=reasons,
        )

    # ------------------------------------------------------------------------
    # Consequence Assessment via MCPv2Stack
    # ------------------------------------------------------------------------

    def assess_consequence_eligibility(
        self,
        agent_id: str,
        capability_id: str,
        request: Dict[str, Any],
        *,
        metric: CurrentMetric | None = None,
        trust_score: float = 100.0,
        system_policy: Any | None = None,
        owner_policy: Any | None = None,
        runtime_policy: Any | None = None,
        at: datetime | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Invoke MCPv2Stack.pre_execution_assessment() for Safety -> Intelligence -> Governance."""
        if metric is None:
            # Clean baseline metric by default
            metric = CurrentMetric(
                requests_per_hour=1.0,
                failure_rate=0.0,
                new_capabilities=[],
                time_of_day=datetime.now(timezone.utc).hour,
                requests_in_window=1,
            )

        return self._stack.pre_execution_assessment(
            agent_id=agent_id,
            request=request,
            metric=metric,
            trust_score=trust_score,
            capability_id=capability_id,
            system_policy=system_policy,
            owner_policy=owner_policy,
            runtime_policy=runtime_policy,
            at=at,
        )

    # ------------------------------------------------------------------------
    # Execution Dispatch & PGL Evidence Sealing
    # ------------------------------------------------------------------------

    def _next_merkle_index(self) -> int:
        with self._merkle_lock:
            self._merkle_counter += 1
            return self._merkle_counter

    def dispatch_execution(
        self,
        connection_ctx: ConnectionContext,
        capability_ctx: CapabilityContext,
        execution_handler: Callable[..., Any],
        *,
        assessment: Dict[str, Any] | None = None,
        session_auth: Dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Evaluate the 8-way intersection and dispatch consequence execution only if authorized.

        If DENIED:
        - Halts immediately.
        - Consequence execution handler is NEVER called.
        - No action receipt is written.
        - Raises GovernanceDeniedError.

        If ALLOWED:
        - Dispatches to execution_handler.
        - Emits PGL evidence receipt with Merkle evidence hash.
        - Returns execution output, evidence hash, and receipt record.
        """
        if assessment is None:
            assessment = self.assess_consequence_eligibility(
                agent_id=capability_ctx.actor_id,
                capability_id=capability_ctx.capability_id,
                request=capability_ctx.payload,
            )

        intersection = self.evaluate_8way_intersection(
            connection_ctx=connection_ctx,
            capability_ctx=capability_ctx,
            cappo_assessment=assessment,
            session_auth=session_auth,
        )

        # Fail-closed guard: Consequence dominance
        if not intersection.effective_authority or not intersection.cappo_authorized:
            reason = "; ".join(intersection.failure_reasons) or "cappo_authorization: consequence execution denied"
            logger.info("CAPPO governance denied consequence execution: %s", reason)
            raise GovernanceDeniedError(
                reason=reason,
                intersection=intersection,
                evidence=assessment,
            )

        # Authorized consequence execution
        logger.info("CAPPO authorized consequence execution for '%s'", capability_ctx.capability_id)
        raw_result = execution_handler(
            connection_ctx=connection_ctx,
            capability_ctx=capability_ctx,
            **kwargs,
        )

        # Emit PGL Action Receipt with Merkle leaf index and content hash
        receipt_id = f"rcpt-{uuid.uuid4()}"
        merkle_index = self._next_merkle_index()
        evidence_hash = assessment.get("evidence_hash", "")
        actioned_at = datetime.now(timezone.utc).isoformat()

        receipt_payload = {
            "receipt_id": receipt_id,
            "connection_id": connection_ctx.connection_id,
            "capability_id": capability_ctx.capability_id,
            "principal": capability_ctx.actor_id,
            "action": capability_ctx.action or capability_ctx.capability_id,
            "decision": "allow",
            "merkle_leaf_index": merkle_index,
            "evidence_hash": evidence_hash,
            "actioned_at": actioned_at,
        }
        content_hash = sha256_json(receipt_payload)
        receipt_payload["content_hash"] = content_hash

        # Optional PGL Client evidence event sealing
        if self._pgl_client is not None:
            try:
                self._pgl_client.append_evidence_event(
                    certificate_id=receipt_id,
                    event_type="mcp_v2_action_executed",
                    evidence={
                        "receipt": receipt_payload,
                        "evidence_hash": evidence_hash,
                    },
                    agent_id=capability_ctx.actor_id,
                )
            except Exception as e:
                logger.warning("Optional PGL ledger evidence event sealing skipped/failed: %s", e)

        return {
            "execution_result": raw_result,
            "status": "success",
            "evidence_hash": evidence_hash,
            "receipt": receipt_payload,
            "intersection": intersection.to_dict(),
        }

    # ------------------------------------------------------------------------
    # Response & Error Formatting
    # ------------------------------------------------------------------------

    def format_response(
        self,
        execution_result: Any,
        connection_id: str,
        *,
        request_id: str | int | None = None,
        evidence_hash: str | None = None,
        receipt: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Format standard MCP JSON-RPC 2.0 success response with durable evidence."""
        content_item = {
            "type": "text",
            "text": json.dumps(execution_result) if isinstance(execution_result, (dict, list)) else str(execution_result),
        }
        result_data: Dict[str, Any] = {
            "content": [content_item],
            "connection_id": connection_id,
            "status": "success",
        }
        if evidence_hash:
            result_data["evidence_hash"] = evidence_hash
        if receipt:
            result_data["receipt"] = receipt

        return {
            "jsonrpc": "2.0",
            "id": request_id if request_id is not None else str(uuid.uuid4()),
            "result": result_data,
        }

    def format_error(
        self,
        code: int | str,
        message: str,
        connection_id: str | None = None,
        *,
        request_id: str | int | None = None,
        data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Format standard MCP JSON-RPC 2.0 error response."""
        error_payload: Dict[str, Any] = {
            "code": code,
            "message": message,
        }
        err_data = data or {}
        if connection_id:
            err_data["connection_id"] = connection_id
        if err_data:
            error_payload["data"] = err_data

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": error_payload,
        }

    # ------------------------------------------------------------------------
    # Full End-to-End Coordination Entrypoint
    # ------------------------------------------------------------------------

    def handle_tool_call(
        self,
        raw_request: Dict[str, Any],
        headers: Dict[str, str] | None = None,
        execution_handler: Callable[..., Any] | None = None,
        session_auth: Dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """High-level entrypoint executing the full governed MCP pipeline.

        Perimeter Defense -> Normalize -> Assess -> 8-Way Intersection -> Dispatch -> Format.
        """
        if not isinstance(raw_request, dict):
            return self.format_error(
                code=-32600,
                message="Request payload must be a JSON-RPC 2.0 object",
                request_id=None,
                data={"error_type": "INVALID_JSONRPC_PAYLOAD"},
            )

        headers = headers or {}
        request_id = raw_request.get("id")

        # 1. Perimeter defense (Replay and Session checks)
        try:
            self.validate_perimeter(headers=headers, raw_request=raw_request, session_auth=session_auth)
        except PerimeterDefenseError as pde:
            return self.format_error(
                code=pde.rpc_code,
                message=pde.message,
                request_id=request_id,
                data={"error_type": pde.code},
            )

        # 2. Semantic normalization
        try:
            conn_ctx, cap_ctx, veklom_agent, auth_bundle = self.normalize_request(
                raw_request=raw_request,
                headers=headers,
            )
        except Exception as e:
            return self.format_error(
                code=-32602,
                message=f"Request normalization failed: {e}",
                request_id=request_id,
            )

        # Default mock handler if none provided
        if execution_handler is None:
            def execution_handler(*args: Any, **h_kwargs: Any) -> Dict[str, Any]:
                return {"executed": True, "action": cap_ctx.capability_id}

        # 3. Consequence assessment & 4. Dispatch execution
        try:
            assessment = self.assess_consequence_eligibility(
                agent_id=cap_ctx.actor_id,
                capability_id=cap_ctx.capability_id,
                request=cap_ctx.payload,
                system_policy=kwargs.get("system_policy"),
                owner_policy=kwargs.get("owner_policy"),
                runtime_policy=kwargs.get("runtime_policy"),
                trust_score=kwargs.get("trust_score", 100.0),
                metric=kwargs.get("metric"),
            )

            dispatch_res = self.dispatch_execution(
                connection_ctx=conn_ctx,
                capability_ctx=cap_ctx,
                execution_handler=execution_handler,
                assessment=assessment,
                session_auth=session_auth,
            )

            return self.format_response(
                execution_result=dispatch_res["execution_result"],
                connection_id=conn_ctx.connection_id,
                request_id=request_id,
                evidence_hash=dispatch_res["evidence_hash"],
                receipt=dispatch_res["receipt"],
            )

        except GovernanceDeniedError as gde:
            return self.format_error(
                code=-32003,
                message="CAPPO_GOVERNANCE_DENIED",
                connection_id=conn_ctx.connection_id,
                request_id=request_id,
                data={
                    "reason": gde.reason,
                    "evidence_hash": gde.evidence.get("evidence_hash") if gde.evidence else None,
                    "intersection": gde.intersection.to_dict() if gde.intersection else None,
                },
            )
        except Exception as e:
            logger.exception("Unexpected execution error in MCP adapter: %s", e)
            return self.format_error(
                code=-32603,
                message=f"Internal execution error: {e}",
                connection_id=conn_ctx.connection_id,
                request_id=request_id,
            )


# ============================================================================
# FACTORY
# ============================================================================

def create_mcp_adapter(
    stack: MCPv2Stack | None = None,
    db: Any | None = None,
    pgl_client: Any | None = None,
    settings: Settings | None = None,
) -> MCPPort:
    """Factory creating the canonical MCPv2Adapter satisfying MCPPort."""
    return MCPv2Adapter(
        stack=stack,
        db=db,
        pgl_client=pgl_client,
        settings=settings,
    )
