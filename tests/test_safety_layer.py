"""MCPAPI v2.0 Safety Layer tests.

Covers behavioural baseline construction, z-score anomaly detection, request
quarantine, and the M-of-N approval quorum (including approver trust floor and
deadline auto-deny).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cappo_backend.services.safety import (
    AnomalyDetectionService,
    ApproverTrustError,
    BehavioralBaselineService,
    CurrentMetric,
    Observation,
    RequestQuarantineService,
)


def _obs(rph: int, fail: float, *, hour: int = 12, caps: tuple[str, ...] = ("search",)) -> Observation:
    obs_time = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    return Observation(
        timestamp=obs_time,
        requests_in_window=rph,
        failure_rate=fail,
        capabilities_used=caps,
    )


def _seed_baseline(svc: BehavioralBaselineService, agent_id: str, n: int = 60) -> None:
    for _ in range(n):
        svc.record_observation(agent_id, _obs(10, 0.05))


def test_baseline_needs_minimum_observations() -> None:
    svc = BehavioralBaselineService()
    for _ in range(5):
        svc.record_observation("a1", _obs(10, 0.05))
    baseline = svc.build_baseline("a1")
    assert baseline.confidence_score == 0
    assert baseline.avg_requests_per_hour == 0


def test_baseline_statistics_and_lock() -> None:
    svc = BehavioralBaselineService()
    _seed_baseline(svc, "a1", n=60)
    baseline = svc.build_baseline("a1")
    assert baseline.avg_requests_per_hour == pytest.approx(10.0)
    assert baseline.confidence_score == pytest.approx(60.0)
    assert baseline.is_locked is True
    assert 12 in baseline.typical_time_windows
    assert "search" in baseline.typical_capabilities


def _seed_varied(svc: BehavioralBaselineService, agent_id: str, n: int = 60) -> None:
    # Mixed traffic so std-dev > 0, and enough samples for confidence >= 50.
    pattern = [9, 10, 11]
    for i in range(n):
        svc.record_observation(agent_id, _obs(pattern[i % len(pattern)], 0.05))


def test_request_spike_anomaly_detected() -> None:
    baselines = BehavioralBaselineService()
    _seed_varied(baselines, "a1")
    baselines.build_baseline("a1")
    detector = AnomalyDetectionService(baselines)

    anomalies = detector.detect("a1", CurrentMetric(requests_per_hour=200, failure_rate=0.05, time_of_day=12))
    types = {a.anomaly_type for a in anomalies}
    assert "request_spike" in types
    spike = next(a for a in anomalies if a.anomaly_type == "request_spike")
    assert spike.severity in ("medium", "high", "critical")


def test_off_hours_and_new_capability_anomaly() -> None:
    baselines = BehavioralBaselineService()
    for _ in range(60):
        baselines.record_observation("a1", _obs(10, 0.05, hour=12, caps=("search",)))
    baselines.build_baseline("a1")
    detector = AnomalyDetectionService(baselines)

    anomalies = detector.detect(
        "a1",
        CurrentMetric(
            requests_per_hour=10,
            failure_rate=0.05,
            time_of_day=3,
            new_capabilities=("delete_database",),
        ),
    )
    types = {a.anomaly_type for a in anomalies}
    assert "off_hours_activity" in types
    assert "new_capability_access" in types


def test_no_anomaly_without_confident_baseline() -> None:
    baselines = BehavioralBaselineService()
    detector = AnomalyDetectionService(baselines)
    assert detector.detect("unknown", CurrentMetric(1000, 0.9, 3)) == []


def test_quarantine_quorum_reached() -> None:
    baselines = BehavioralBaselineService()
    _seed_varied(baselines, "a1")
    baselines.build_baseline("a1")
    detector = AnomalyDetectionService(baselines)
    anomalies = detector.detect("a1", CurrentMetric(requests_per_hour=500, failure_rate=0.05, time_of_day=12))

    q = RequestQuarantineService(approvers_required=2)
    qr = q.quarantine({"agent_id": "a1"}, anomalies)
    assert qr.status == "quarantined"

    # First approval does not reach quorum.
    assert q.approve(qr.quarantine_id, "approver-1", approver_trust=95) is False
    # Second distinct approver reaches quorum.
    assert q.approve(qr.quarantine_id, "approver-2", approver_trust=88) is True
    assert q.get(qr.quarantine_id).status == "approved"


def test_low_trust_approver_rejected() -> None:
    q = RequestQuarantineService(approvers_required=1)
    from cappo_backend.services.safety import AnomalyDetection

    anomaly = AnomalyDetection(
        detection_id="d1",
        agent_id="a1",
        detected_at=datetime.now(timezone.utc),
        anomaly_type="request_spike",
        deviation_score=4.0,
        anomaly_score=90.0,
        severity="critical",
        recommended_action="block",
        evidence_hash="x",
    )
    qr = q.quarantine({"agent_id": "a1"}, [anomaly])
    with pytest.raises(ApproverTrustError):
        q.approve(qr.quarantine_id, "weak-approver", approver_trust=50)


def test_quarantine_auto_deny_on_deadline() -> None:
    q = RequestQuarantineService(approvers_required=2, hold_seconds=-1)
    qr = q.quarantine({"agent_id": "a1"}, [])
    qr.approval_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    processed = q.process_expired()
    assert qr in processed
    assert q.get(qr.quarantine_id).status == "denied"
