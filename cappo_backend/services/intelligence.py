"""MCPAPI v2.0 — Intelligence Layer (learning & prediction).

Python port of the MCPAPI v2.0 intelligence-layer reference. Provides:

- :class:`CostAttributionService` — per-agent / per-capability cost ledger with
  budget enforcement and overage policy.
- :class:`RiskScoringService` — weighted risk profile (0–100) + threat level and
  recommended actions.
- :class:`AnomalyCorrelationService` — links anomaly signals observed close in
  time to surface coordinated/correlated attacks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from cappo_backend.services.canonical import sha256_json

ThreatLevel = Literal["green", "yellow", "orange", "red"]
Severity = Literal["low", "medium", "high", "critical"]
OveragePolicy = Literal["deny", "escalate", "auto-approve-charge"]
CostAction = Literal["allowed", "escalated", "denied", "auto_charged"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CostAllocationRecord:
    record_id: str
    agent_id: str
    capability_id: str
    timestamp: datetime
    cost: float
    budget_before: float
    budget_after: float
    budget_exceeded: bool
    action_taken: CostAction
    evidence_hash: str


@dataclass
class _Budget:
    used: float = 0.0
    budget: float = 0.0


class CostAttributionService:
    """Tracks per-agent capability cost against budgets."""

    def __init__(self) -> None:
        # (agent_id, capability_id) -> budget bucket
        self._budgets: dict[tuple[str, str], _Budget] = {}
        self._records: list[CostAllocationRecord] = []

    def set_budget(self, agent_id: str, capability_id: str, budget: float) -> None:
        bucket = self._budgets.setdefault((agent_id, capability_id), _Budget())
        bucket.budget = budget

    def remaining(self, agent_id: str, capability_id: str) -> float:
        bucket = self._budgets.get((agent_id, capability_id))
        if bucket is None:
            return 0.0
        return bucket.budget - bucket.used

    def record_cost(
        self,
        agent_id: str,
        capability_id: str,
        cost: float,
        *,
        overage_policy: OveragePolicy = "deny",
    ) -> CostAllocationRecord:
        bucket = self._budgets.setdefault((agent_id, capability_id), _Budget())
        before = bucket.budget - bucket.used
        exceeded = (bucket.used + cost) > bucket.budget

        action: CostAction = "allowed"
        if exceeded:
            if overage_policy == "deny":
                action = "denied"
            elif overage_policy == "escalate":
                action = "escalated"
            else:
                action = "auto_charged"

        if action in ("allowed", "auto_charged"):
            bucket.used += cost

        after = bucket.budget - bucket.used
        record = CostAllocationRecord(
            record_id=str(uuid.uuid4()),
            agent_id=agent_id,
            capability_id=capability_id,
            timestamp=_now(),
            cost=cost,
            budget_before=before,
            budget_after=after,
            budget_exceeded=exceeded,
            action_taken=action,
            evidence_hash=sha256_json(
                {"agent": agent_id, "cap": capability_id, "cost": cost, "action": action}
            ),
        )
        self._records.append(record)
        return record

    def total_cost(self, agent_id: str) -> float:
        return sum(
            r.cost for r in self._records if r.agent_id == agent_id and r.action_taken != "denied"
        )

    def records_for(self, agent_id: str) -> list[CostAllocationRecord]:
        return [r for r in self._records if r.agent_id == agent_id]


@dataclass
class RiskFactorInputs:
    trust_score: float = 100.0
    anomaly_score: float = 0.0
    cost_anomaly_score: float = 0.0
    significant_behavioral_change: bool = False
    high_failure_rate: bool = False
    policy_violations: int = 0


@dataclass
class RiskFactor:
    factor_name: str
    contribution: float
    severity: Severity
    mitigations: tuple[str, ...] = ()


@dataclass
class RiskProfile:
    agent_id: str
    overall_risk_score: float
    risk_factors: list[RiskFactor]
    threat_level: ThreatLevel
    last_assessed: datetime
    recommended_actions: tuple[str, ...]
    evidence_hash: str


_RECOMMENDATIONS: dict[ThreatLevel, tuple[str, ...]] = {
    "green": ("Continue normal monitoring",),
    "yellow": ("Increase audit logging", "Monitor for further anomalies"),
    "orange": (
        "Require approval for sensitive capabilities",
        "Apply temporary trust suppression",
        "Alert security team",
    ),
    "red": (
        "Quarantine agent requests",
        "Require M-of-N approval quorum",
        "Escalate to incident response",
    ),
}


class RiskScoringService:
    """Computes a weighted risk profile and threat level for an agent."""

    def __init__(self) -> None:
        self._profiles: dict[str, RiskProfile] = {}

    def assess(self, agent_id: str, factors: RiskFactorInputs) -> RiskProfile:
        risk_factors: list[RiskFactor] = []

        if factors.trust_score < 50:
            risk_factors.append(
                RiskFactor(
                    "Low trust score",
                    20.0,
                    "critical" if factors.trust_score < 30 else "high",
                    ("Re-verify agent identity", "Restrict capabilities"),
                )
            )
        if factors.anomaly_score > 50:
            risk_factors.append(
                RiskFactor(
                    "Behavioral anomalies",
                    25.0,
                    "critical" if factors.anomaly_score > 80 else "high",
                    ("Quarantine suspicious requests",),
                )
            )
        if factors.cost_anomaly_score > 50:
            risk_factors.append(
                RiskFactor(
                    "Unusual cost pattern",
                    15.0,
                    "high" if factors.cost_anomaly_score > 75 else "medium",
                    ("Review spend", "Apply budget caps"),
                )
            )
        if factors.significant_behavioral_change:
            risk_factors.append(RiskFactor("Significant behavioral change", 20.0, "high"))
        if factors.high_failure_rate:
            risk_factors.append(RiskFactor("High failure rate", 10.0, "medium"))
        if factors.policy_violations > 0:
            risk_factors.append(RiskFactor("Multiple policy violations", 20.0, "critical"))

        overall = min(100.0, sum(f.contribution for f in risk_factors))
        threat = self._threat_level(overall)

        profile = RiskProfile(
            agent_id=agent_id,
            overall_risk_score=overall,
            risk_factors=risk_factors,
            threat_level=threat,
            last_assessed=_now(),
            recommended_actions=_RECOMMENDATIONS[threat],
            evidence_hash=sha256_json({"agent": agent_id, "score": overall, "threat": threat}),
        )
        self._profiles[agent_id] = profile
        return profile

    @staticmethod
    def _threat_level(score: float) -> ThreatLevel:
        if score < 30:
            return "green"
        if score < 50:
            return "yellow"
        if score < 75:
            return "orange"
        return "red"

    def needs_intervention(self, agent_id: str) -> bool:
        profile = self._profiles.get(agent_id)
        return profile is not None and profile.threat_level in ("orange", "red")


@dataclass
class AnomalySignal:
    agent_id: str
    anomaly_type: str
    severity: Severity
    detected_at: datetime = field(default_factory=_now)


@dataclass
class CorrelationAnalysis:
    analysis_id: str
    timestamp: datetime
    agent_id: str
    signal_count: int
    distinct_types: tuple[str, ...]
    correlated: bool
    severity: Severity
    evidence_hash: str


class AnomalyCorrelationService:
    """Links anomaly signals seen close in time to detect coordinated attacks."""

    def __init__(self, *, window_seconds: int = 300, correlation_threshold: int = 3) -> None:
        self._signals: list[AnomalySignal] = []
        self._window = timedelta(seconds=window_seconds)
        self._threshold = correlation_threshold

    def record(self, signal: AnomalySignal) -> None:
        self._signals.append(signal)

    def correlate(self, agent_id: str, *, at: datetime | None = None) -> CorrelationAnalysis:
        at = at or _now()
        window_start = at - self._window
        relevant = [
            s
            for s in self._signals
            if s.agent_id == agent_id and window_start <= s.detected_at <= at
        ]
        distinct = tuple(sorted({s.anomaly_type for s in relevant}))
        correlated = len(relevant) >= self._threshold
        severity: Severity = "low"
        if any(s.severity == "critical" for s in relevant):
            severity = "critical"
        elif correlated:
            severity = "high"
        elif len(relevant) >= 2:
            severity = "medium"

        return CorrelationAnalysis(
            analysis_id=str(uuid.uuid4()),
            timestamp=at,
            agent_id=agent_id,
            signal_count=len(relevant),
            distinct_types=distinct,
            correlated=correlated,
            severity=severity,
            evidence_hash=sha256_json(
                {"agent": agent_id, "count": len(relevant), "types": distinct}
            ),
        )
