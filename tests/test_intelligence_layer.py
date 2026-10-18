"""MCPAPI v2.0 Intelligence Layer tests.

Covers cost attribution + budget enforcement, weighted risk scoring / threat
levels, and anomaly correlation within a time window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cappo_backend.services.intelligence import (
    AnomalyCorrelationService,
    AnomalySignal,
    CostAttributionService,
    RiskFactorInputs,
    RiskScoringService,
)


def test_cost_attribution_within_budget() -> None:
    svc = CostAttributionService()
    svc.set_budget("a1", "search", budget=100)
    rec = svc.record_cost("a1", "search", 30)
    assert rec.action_taken == "allowed"
    assert rec.budget_exceeded is False
    assert svc.remaining("a1", "search") == 70
    assert svc.total_cost("a1") == 30


def test_cost_attribution_overage_deny() -> None:
    svc = CostAttributionService()
    svc.set_budget("a1", "search", budget=50)
    rec = svc.record_cost("a1", "search", 80, overage_policy="deny")
    assert rec.budget_exceeded is True
    assert rec.action_taken == "denied"
    # Denied spend is not applied.
    assert svc.remaining("a1", "search") == 50
    assert svc.total_cost("a1") == 0


def test_cost_attribution_auto_charge() -> None:
    svc = CostAttributionService()
    svc.set_budget("a1", "search", budget=50)
    rec = svc.record_cost("a1", "search", 80, overage_policy="auto-approve-charge")
    assert rec.action_taken == "auto_charged"
    assert svc.remaining("a1", "search") == -30


def test_risk_scoring_green_when_clean() -> None:
    svc = RiskScoringService()
    profile = svc.assess("a1", RiskFactorInputs(trust_score=95))
    assert profile.overall_risk_score == 0
    assert profile.threat_level == "green"
    assert svc.needs_intervention("a1") is False


def test_risk_scoring_red_with_multiple_factors() -> None:
    svc = RiskScoringService()
    profile = svc.assess(
        "a1",
        RiskFactorInputs(
            trust_score=20,
            anomaly_score=90,
            cost_anomaly_score=80,
            policy_violations=2,
        ),
    )
    # 20 + 25 + 15 + 20 = 80 -> capped at 100, red.
    assert profile.overall_risk_score >= 75
    assert profile.threat_level == "red"
    assert svc.needs_intervention("a1") is True
    assert "Quarantine agent requests" in profile.recommended_actions


def test_risk_scoring_thresholds() -> None:
    svc = RiskScoringService()
    # Only behavioral anomaly (25) -> green (<30).
    p1 = svc.assess("g", RiskFactorInputs(trust_score=100, anomaly_score=60))
    assert p1.threat_level == "green"
    # trust(20) + anomaly(25) = 45 -> yellow.
    p2 = svc.assess("y", RiskFactorInputs(trust_score=20, anomaly_score=60))
    assert p2.threat_level == "yellow"
    # trust(20) + anomaly(25) + cost(15) = 60 -> orange.
    p3 = svc.assess("o", RiskFactorInputs(trust_score=20, anomaly_score=60, cost_anomaly_score=60))
    assert p3.threat_level == "orange"
    assert svc.needs_intervention("o") is True


def test_anomaly_correlation_window() -> None:
    svc = AnomalyCorrelationService(window_seconds=300, correlation_threshold=3)
    base = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    for i, kind in enumerate(["request_spike", "failure_spike", "off_hours_activity"]):
        svc.record(AnomalySignal("a1", kind, "high", detected_at=base + timedelta(seconds=i * 10)))
    analysis = svc.correlate("a1", at=base + timedelta(seconds=60))
    assert analysis.signal_count == 3
    assert analysis.correlated is True
    assert analysis.severity in ("high", "critical")
    assert set(analysis.distinct_types) == {"request_spike", "failure_spike", "off_hours_activity"}


def test_anomaly_correlation_outside_window_excluded() -> None:
    svc = AnomalyCorrelationService(window_seconds=60, correlation_threshold=2)
    base = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    svc.record(AnomalySignal("a1", "request_spike", "low", detected_at=base))
    svc.record(AnomalySignal("a1", "failure_spike", "low", detected_at=base - timedelta(seconds=600)))
    analysis = svc.correlate("a1", at=base)
    assert analysis.signal_count == 1
    assert analysis.correlated is False
