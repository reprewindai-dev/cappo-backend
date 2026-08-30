"""Side-effect-free pre-execution authorization evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.governance import Policy, PolicyRule
from cappo_backend.services.mcp_v2 import CurrentMetric, get_mcp_v2_stack

_ALLOW_DIRECTIVES = {"ALLOW", "ALLOW_WITH_AUDIT"}
_DENY_DIRECTIVES = {"DENY", "REJECT", "REJECTED"}
_APPROVAL_DIRECTIVES = {"NEEDS_APPROVAL", "REQUIRE_APPROVAL", "PENDING_APPROVAL"}
_RISK_TIERS = {"standard", "elevated", "critical"}


@dataclass(frozen=True)
class DirectiveDecision:
    """Normalized legacy directive decision used by the execution pipeline."""

    directive: str
    risk_tier: str


def normalize_directive(payload: dict[str, Any], *, strict: bool) -> DirectiveDecision:
    """Normalize execution governance fields without changing legacy defaults."""
    raw_directive = payload.get("directive")
    directive = str(raw_directive).strip().upper() if raw_directive is not None else ""
    if not directive:
        directive = "NEEDS_APPROVAL" if strict else "ALLOW"
    raw_risk = payload.get("risk_tier")
    risk_tier = str(raw_risk).strip().lower() if raw_risk is not None else ""
    if not risk_tier:
        risk_tier = "standard"
    if risk_tier not in _RISK_TIERS:
        raise ValueError("risk_tier is not a supported governance lane")
    return DirectiveDecision(directive=directive, risk_tier=risk_tier)


def evaluate_authorization(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate local governance and safety policy without execution side effects.

    This deliberately does not construct an orchestrator, open a provider
    connection, mint execution artifacts, write a run, or call an upstream.
    """
    decision_frame: dict[str, Any] = {
        "agent_id": payload.get("agent_id"),
        "capability_id": payload.get("capability_id", "exec"),
        "request": payload.get("request") or {},
        "trust_score": payload.get("trust_score", 75.0),
        "time_of_day": payload.get("time_of_day", 12),
        "directive": payload.get("directive"),
        "risk_tier": payload.get("risk_tier"),
    }
    try:
        if not decision_frame["agent_id"]:
            raise ValueError("agent_id is required")
        normalized = normalize_directive(payload, strict=True)
        if normalized.directive in _DENY_DIRECTIVES:
            decision = "REJECTED"
            reason = "directive denies execution"
        else:
            requires_approval = normalized.directive in _APPROVAL_DIRECTIVES
            if normalized.directive not in _ALLOW_DIRECTIVES | _APPROVAL_DIRECTIVES:
                decision = "REJECTED"
                reason = "unsupported directive"
            else:
                policy = Policy(
                    policy_id="request-directive",
                    rules=[
                        PolicyRule(
                            effect="allow"
                            if normalized.directive in _ALLOW_DIRECTIVES | _APPROVAL_DIRECTIVES
                            else "deny"
                        )
                    ],
                    requires_approval=requires_approval or normalized.risk_tier == "critical",
                )
                metric = CurrentMetric(
                    requests_per_hour=float(payload.get("requests_per_hour", 0.0)),
                    failure_rate=float(payload.get("failure_rate", 0.0)),
                    time_of_day=int(payload.get("time_of_day", 12)),
                )
                evidence = get_mcp_v2_stack().pre_execution_assessment(
                    str(decision_frame["agent_id"]),
                    decision_frame["request"],
                    metric=metric,
                    trust_score=float(decision_frame["trust_score"]),
                    capability_id=str(decision_frame["capability_id"]),
                    runtime_policy=policy,
                    at=datetime.now(timezone.utc).replace(
                        hour=int(decision_frame["time_of_day"]),
                        minute=0,
                        second=0,
                        microsecond=0,
                    ),
                )
                governance = evidence["governance"]
                if not evidence["allow"]:
                    decision = "REJECTED"
                    reason = "safety assessment denied the request"
                elif not governance["is_valid"] or not governance["policy_allows"]:
                    decision = "REJECTED"
                    reason = "governance policy denied the request"
                elif governance["requires_approval"]:
                    decision = "NEEDS_APPROVAL"
                    reason = "governance policy requires approval"
                else:
                    decision = "APPROVED"
                    reason = "governance policy approved the request"
                decision_frame["evidence"] = evidence
    except Exception as exc:
        decision = "REJECTED"
        reason = "authorization policy evaluation failed"
        decision_frame["failure"] = exc.__class__.__name__

    lane = str(decision_frame.get("risk_tier") or "standard").lower()
    if lane not in _RISK_TIERS:
        lane = "standard"
    evidence_hash = sha256_json(decision_frame)
    return {
        "decision": decision,
        "lane": lane,
        "reason": reason,
        "decision_hash": sha256_json(
            {"decision": decision, "lane": lane, "reason": reason, "evidence_hash": evidence_hash}
        ),
        "evidence_hash": evidence_hash,
    }


__all__ = ["DirectiveDecision", "evaluate_authorization", "normalize_directive"]
