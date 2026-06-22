"""MCPAPI v2.0 governance stack facade.

Bundles the Safety, Intelligence, and Governance layer services into a single
process-wide stack so the API surface (and, optionally, the orchestrator) share
the same behavioural baselines, risk profiles, quarantine queue, and delegation
chains.

The :meth:`MCPv2Stack.pre_execution_assessment` method runs the v2 phases
(Safety → Intelligence → Governance) for a single request and returns a
structured, deterministic evidence dict suitable for audit persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.governance import (
    DelegationChainTracker,
    EffectivePolicy,
    Policy,
    PolicyCompositionEngine,
    TemporalPolicyResolver,
    effective_permissions,
)
from cappo_backend.services.intelligence import (
    AnomalyCorrelationService,
    AnomalySignal,
    CostAttributionService,
    RiskFactorInputs,
    RiskScoringService,
)
from cappo_backend.services.safety import (
    AnomalyDetectionService,
    BehavioralBaselineService,
    CurrentMetric,
    Observation,
    RequestQuarantineService,
)


@dataclass
class MCPv2Stack:
    """Holds singletons for all three v2 governance layers."""

    baselines: BehavioralBaselineService = field(default_factory=BehavioralBaselineService)
    cost: CostAttributionService = field(default_factory=CostAttributionService)
    risk: RiskScoringService = field(default_factory=RiskScoringService)
    correlation: AnomalyCorrelationService = field(default_factory=AnomalyCorrelationService)
    quarantine: RequestQuarantineService = field(default_factory=RequestQuarantineService)
    policy: PolicyCompositionEngine = field(default_factory=PolicyCompositionEngine)
    delegation: DelegationChainTracker = field(default_factory=DelegationChainTracker)
    temporal: TemporalPolicyResolver = field(
        default_factory=lambda: TemporalPolicyResolver(TemporalPolicyResolver.business_hours())
    )

    def __post_init__(self) -> None:
        self.anomaly = AnomalyDetectionService(self.baselines)

    def pre_execution_assessment(
        self,
        agent_id: str,
        request: dict,
        *,
        metric: CurrentMetric,
        trust_score: float,
        capability_id: str = "exec",
        system_policy: Policy | None = None,
        owner_policy: Policy | None = None,
        runtime_policy: Policy | None = None,
    ) -> dict:
        """Run Safety → Intelligence → Governance phases for one request.

        Returns a structured evidence dict. ``allow`` is False when an anomaly
        recommends ``block`` or quarantine is required, or when the composed
        policy denies the capability for the current trust level.
        """
        # --- Safety phase ------------------------------------------------
        anomalies = self.anomaly.detect(agent_id, metric)
        for a in anomalies:
            self.correlation.record(
                AnomalySignal(agent_id, a.anomaly_type, a.severity, a.detected_at)
            )
        correlation = self.correlation.correlate(agent_id)

        worst_action = "log"
        for a in anomalies:
            if a.recommended_action == "block":
                worst_action = "block"
                break
            if a.recommended_action == "quarantine":
                worst_action = "quarantine"

        quarantine_id: str | None = None
        if worst_action in ("block", "quarantine"):
            qr = self.quarantine.quarantine(request, anomalies)
            quarantine_id = qr.quarantine_id

        # --- Intelligence phase -----------------------------------------
        max_anomaly_score = max((a.anomaly_score for a in anomalies), default=0.0)
        risk = self.risk.assess(
            agent_id,
            RiskFactorInputs(
                trust_score=trust_score,
                anomaly_score=max_anomaly_score,
                significant_behavioral_change=correlation.correlated,
            ),
        )

        # --- Governance phase -------------------------------------------
        composition = self.policy.compose(
            agent_id,
            capability_id,
            system_policy=system_policy,
            owner_policy=owner_policy,
            runtime_policy=runtime_policy,
        )
        effective: EffectivePolicy = self.temporal.resolve(composition.effective_policy)
        perms = effective_permissions(
            composition,
            trust_current=self.delegation.effective_trust(capability_id, trust_score),
        )

        policy_allows = (
            perms.can_execute if (system_policy or owner_policy or runtime_policy) else True
        )

        allow = worst_action != "block" and quarantine_id is None and policy_allows

        evidence = {
            "agent_id": agent_id,
            "allow": allow,
            "safety": {
                "anomalies": [a.anomaly_type for a in anomalies],
                "recommended_action": worst_action,
                "quarantine_id": quarantine_id,
                "correlated": correlation.correlated,
            },
            "intelligence": {
                "risk_score": risk.overall_risk_score,
                "threat_level": risk.threat_level,
                "needs_intervention": self.risk.needs_intervention(agent_id),
            },
            "governance": {
                "policy_allows": policy_allows,
                "requires_approval": effective.requires_approval,
                "trust_required": effective.trust_required,
                "conflicts": [c.conflict_type for c in composition.conflicts_detected],
                "is_valid": composition.is_valid,
            },
        }
        evidence["evidence_hash"] = sha256_json(evidence)
        return evidence


_STACK: MCPv2Stack | None = None


def get_mcp_v2_stack() -> MCPv2Stack:
    """Return the process-wide v2 governance stack (lazy singleton)."""
    global _STACK
    if _STACK is None:
        _STACK = MCPv2Stack()
    return _STACK


def reset_mcp_v2_stack() -> None:
    """Reset the singleton (test helper)."""
    global _STACK
    _STACK = None


# Re-exports for convenient observation recording.
__all__ = ["MCPv2Stack", "get_mcp_v2_stack", "reset_mcp_v2_stack", "CurrentMetric", "Observation"]
