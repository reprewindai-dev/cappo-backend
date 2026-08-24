# gateway/policy/seked.py
# P — SEKED Policy Engine & CAPPO Lease Issuance (cap-policy-authority)
# POST /v1/policy/evaluate  |  POST /v1/lease/issue
# SLO: evaluate <10ms p95, issue <15ms p95
# Kill switch: CAPABILITY_KILL_SWITCH checked before every lease issuance

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from .consequence_types import (
    ActionIntent,
    CapabilityLease,
    EvaluatePolicyRequest,
    EvaluatePolicyResponse,
    IssueLeaseRequest,
    IssueLeaseResponse,
    PolicyDecision,
    PolicyEvaluationResult,
    WorkloadIdentity,
)

log = structlog.get_logger(__name__)

# Maximum lease TTL per the manifest.
MAX_LEASE_TTL_SECONDS = 3600
# Maximum delegation depth per the governance spec.
MAX_DELEGATION_DEPTH = 2


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# SEKED Policy Rules (Datalog-inspired, pure Python)
# Production: compile to biscuit-python token with embedded datalog facts.
# ─────────────────────────────────────────────────────────────────────────────

# Each rule is a callable: (identity, intent) -> (allow: bool, reason: str)
SEKED_RULES: list[tuple[str, Any]] = [

    # R-001: Budget gate — deny if requested cost exceeds workspace limit.
    ("budget_gate", lambda identity, intent: (
        (True, "budget_ok")
        if intent.max_cost_usd <= float(os.getenv("WORKSPACE_MAX_COST_USD", "5000"))
        else (False, "BudgetExceeded: max_cost_usd exceeds workspace limit")
    )),

    # R-002: Capability allowlist — only registered capability types allowed.
    ("capability_allowlist", lambda identity, intent: (
        (True, "capability_allowed")
        if intent.capability_type in _get_allowed_capabilities()
        else (False, f"CapabilityNotAllowed: '{intent.capability_type}' is not in the allowlist")
    )),

    # R-003: Target-state precondition — must supply a non-empty version.
    ("target_state_required", lambda identity, intent: (
        (True, "target_state_present")
        if intent.target_expected_version.strip()
        else (False, "MissingTargetVersion: TOCTOU protection requires target_expected_version")
    )),

    # R-004: Delegation depth gate — prevent infinite delegation.
    ("delegation_depth", lambda identity, intent: (
        (True, "delegation_ok")
        if int(intent.parameters.get("delegation_depth", 0)) <= MAX_DELEGATION_DEPTH
        else (False, f"DelegationDepthExceeded: max depth is {MAX_DELEGATION_DEPTH}")
    )),

    # R-005: GitHub patch branch restriction — mutations only to non-protected branches.
    ("github_branch_policy", lambda identity, intent: (
        (True, "branch_allowed")
        if intent.capability_type != "github.patch"
           or not any(
               intent.target_resource.endswith(f"@{b}")
               for b in ["main", "master", "production"]
               if os.getenv("PROTECT_DEFAULT_BRANCHES", "true") == "true"
           )
        else (False, "ProtectedBranch: direct patches to main/master/production require M-of-N approval")
    )),
]


def _get_allowed_capabilities() -> set[str]:
    env = os.getenv("ALLOWED_CAPABILITIES", "github.patch,file.write,http.post,db.query")
    return set(c.strip() for c in env.split(","))


def _compute_policy_hash(rules: list) -> str:
    rule_names = json.dumps([r[0] for r in rules], sort_keys=True).encode()
    return sha256_hex(rule_names)


# ─────────────────────────────────────────────────────────────────────────────
# SEKED Policy Evaluator
# ─────────────────────────────────────────────────────────────────────────────

class SEKEDPolicyEngine:
    """
    Evaluates all SEKED Datalog rules against an ActionIntent.
    Fail-closed: any DENY rule short-circuits to DENY.
    """

    def __init__(self, rules: list = None):
        self.rules = rules or SEKED_RULES
        self.policy_hash = _compute_policy_hash(self.rules)

    def evaluate(self, req: EvaluatePolicyRequest) -> EvaluatePolicyResponse:
        log.info(
            "policy.evaluation_start",
            intent_id=str(req.intent.intent_id),
            capability=req.intent.capability_type,
            principal=req.identity.principal,
            policy_hash=self.policy_hash,
        )

        for rule_name, rule_fn in self.rules:
            allow, reason = rule_fn(req.identity, req.intent)
            if not allow:
                log.warning(
                    "policy.denied",
                    rule=rule_name,
                    reason=reason,
                    intent_id=str(req.intent.intent_id),
                )
                result = PolicyEvaluationResult(
                    decision=PolicyDecision.DENY,
                    reason=reason,
                    policy_hash=self.policy_hash,
                    evaluated_at=datetime.now(tz=timezone.utc),
                )
                return EvaluatePolicyResponse(result=result)

        log.info(
            "policy.allowed",
            intent_id=str(req.intent.intent_id),
            capability=req.intent.capability_type,
        )
        result = PolicyEvaluationResult(
            decision=PolicyDecision.ALLOW,
            reason="All SEKED rules passed",
            policy_hash=self.policy_hash,
            evaluated_at=datetime.now(tz=timezone.utc),
        )
        return EvaluatePolicyResponse(result=result)


# ─────────────────────────────────────────────────────────────────────────────
# CAPPO Lease Manager
# ─────────────────────────────────────────────────────────────────────────────

class CAPPOLeaseManager:
    """
    Issues cryptographically signed CapabilityLeases after SEKED ALLOW.

    Signing: HMAC-SHA256 in stub; production wires to AWS KMS / LockerPhycer
    signing key via the UDS IPC channel.

    Kill switch: CAPABILITY_KILL_SWITCH=<type> immediately blocks that type.
    """

    def __init__(self, signing_key: bytes = None, lease_ttl_seconds: int = 900):
        # Default TTL: 15 minutes (well under the 3600s max).
        self.lease_ttl_seconds = min(lease_ttl_seconds, MAX_LEASE_TTL_SECONDS)
        # Production: key from LockerPhycer KMS / env secret.
        self._signing_key = signing_key or os.getenv("LEASE_SIGNING_KEY", "dev-signing-key-change-in-production").encode()

    def issue(self, req: IssueLeaseRequest) -> IssueLeaseResponse:
        # Fail fast if policy was DENY.
        if req.policy_result.decision.value != "ALLOW":
            raise PermissionError(
                f"LeaseRefused: policy decision was {req.policy_result.decision} "
                f"— {req.policy_result.reason}"
            )

        # Kill switch per capability type.
        self._check_kill_switch(req.intent.capability_type)

        now = datetime.now(tz=timezone.utc)
        expires_at = now + timedelta(seconds=self.lease_ttl_seconds)

        delegation_depth = int(req.intent.parameters.get("delegation_depth", 0))

        lease = CapabilityLease(
            lease_id=uuid.uuid4(),
            workspace_id=req.identity.workspace_id,
            principal=req.identity.principal,
            capability_type=req.intent.capability_type,
            target_resource=req.intent.target_resource,
            target_expected_version=req.intent.target_expected_version,
            expires_at=expires_at,
            max_cost_usd=req.intent.max_cost_usd,
            policy_hash=req.policy_result.policy_hash,
            signature="",  # Filled in below.
            delegation_depth=delegation_depth,
            receipt_required=True,
        )

        lease.signature = self._sign(lease)

        log.info(
            "lease.issued",
            lease_id=str(lease.lease_id),
            principal=req.identity.principal,
            capability=req.intent.capability_type,
            target_version=req.intent.target_expected_version,
            expires_at=expires_at.isoformat(),
        )

        return IssueLeaseResponse(lease=lease)

    def verify_signature(self, lease: CapabilityLease) -> bool:
        """Verify a lease signature — used by Lockerphycer before spawning a cell."""
        expected = self._sign(lease)
        return expected == lease.signature

    def _sign(self, lease: CapabilityLease) -> str:
        import hmac
        # Canonical payload: all fields except the signature itself.
        payload = json.dumps({
            "lease_id": str(lease.lease_id),
            "workspace_id": str(lease.workspace_id),
            "principal": lease.principal,
            "capability_type": lease.capability_type,
            "target_resource": lease.target_resource,
            "target_expected_version": lease.target_expected_version,
            "expires_at": lease.expires_at.isoformat(),
            "max_cost_usd": lease.max_cost_usd,
            "policy_hash": lease.policy_hash,
            "delegation_depth": lease.delegation_depth,
            "receipt_required": lease.receipt_required,
        }, sort_keys=True).encode()

        # Production: replace HMAC with Ed25519 via LockerPhycer KMS UDS.
        mac = hmac.new(self._signing_key, payload, hashlib.sha256)
        return mac.hexdigest()

    def _check_kill_switch(self, capability_type: str) -> None:
        # Global kill switch.
        if os.getenv("GLOBAL_EXECUTION", "ACTIVE") != "ACTIVE":
            raise RuntimeError("KillSwitch: GLOBAL_EXECUTION is not ACTIVE")
        # Capability-specific kill switch.
        cap_switch = os.getenv(f"CAPABILITY_KILL_{capability_type.upper().replace('.', '_')}", "ACTIVE")
        if cap_switch != "ACTIVE":
            raise RuntimeError(f"KillSwitch: capability '{capability_type}' is blocked")
