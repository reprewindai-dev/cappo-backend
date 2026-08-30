"""MCPAPI v2.0 — Safety Layer (breach prevention).

Python port of the MCPAPI v2.0 safety-layer reference. Provides:

- :class:`BehavioralBaselineService` — per-agent behavioural baselines built from
  rolling observations (mean / std-dev of request rate and failure rate, typical
  capabilities, active hours).
- :class:`AnomalyDetectionService` — z-score anomaly detection against a baseline,
  producing severity + recommended action.
- :class:`RequestQuarantineService` — holds suspicious requests pending an M-of-N
  approval quorum, with deadline-based auto-deny.

The services are deterministic and in-memory so they can be unit-tested and
composed into the governed pipeline without a database dependency.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from cappo_backend.services.canonical import sha256_json

Severity = Literal["low", "medium", "high", "critical"]
RecommendedAction = Literal["log", "alert", "quarantine", "block"]
QuarantineStatus = Literal["quarantined", "approved", "denied", "auto_released"]

AnomalyType = Literal[
    "request_spike",
    "failure_spike",
    "new_capability_access",
    "off_hours_activity",
    "unusual_pattern",
    "capability_mutation",
    "delegation_chain_exploit",
]

# Statistical / policy constants (mirrors the reference implementation).
ANOMALY_THRESHOLD_SIGMA = 2.0
BASELINE_MIN_OBSERVATIONS = 10
BASELINE_LOCK_OBSERVATIONS = 30
BASELINE_WINDOW_DAYS = 30
DEFAULT_APPROVERS_REQUIRED = 2
QUARANTINE_HOLD_SECONDS = 60 * 60  # 1 hour
APPROVER_MIN_TRUST = 80.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Observation:
    timestamp: datetime
    requests_in_window: int
    failure_rate: float
    capabilities_used: tuple[str, ...] = ()
    error_type: str | None = None


@dataclass
class BehavioralBaseline:
    agent_id: str
    observation_window_days: int = 0
    avg_requests_per_hour: float = 0.0
    std_dev_requests_per_hour: float = 0.0
    avg_failure_rate: float = 0.0
    std_dev_failure_rate: float = 0.0
    typical_capabilities: dict[str, int] = field(default_factory=dict)
    typical_time_windows: tuple[int, ...] = ()
    typical_error_types: dict[str, int] = field(default_factory=dict)
    confidence_score: float = 0.0
    last_updated: datetime = field(default_factory=_now)
    is_locked: bool = False


@dataclass
class AnomalyDetection:
    detection_id: str
    agent_id: str
    detected_at: datetime
    anomaly_type: AnomalyType
    deviation_score: float
    anomaly_score: float
    severity: Severity
    recommended_action: RecommendedAction
    evidence_hash: str


@dataclass
class CurrentMetric:
    requests_per_hour: float
    failure_rate: float
    time_of_day: int
    requests_in_window: int = 0
    new_capabilities: tuple[str, ...] = ()


class BehavioralBaselineService:
    """Builds and stores per-agent behavioural baselines from observations."""

    def __init__(self) -> None:
        self._observations: dict[str, list[Observation]] = {}
        self._baselines: dict[str, BehavioralBaseline] = {}

    def record_observation(self, agent_id: str, observation: Observation) -> None:
        obs = self._observations.setdefault(agent_id, [])
        obs.append(observation)
        cutoff = _now() - timedelta(days=BASELINE_WINDOW_DAYS)
        self._observations[agent_id] = [o for o in obs if o.timestamp > cutoff]

    def build_baseline(self, agent_id: str) -> BehavioralBaseline:
        obs = self._observations.get(agent_id, [])
        if len(obs) < BASELINE_MIN_OBSERVATIONS:
            baseline = BehavioralBaseline(agent_id=agent_id)
            self._baselines[agent_id] = baseline
            return baseline

        requests = [float(o.requests_in_window) for o in obs]
        failures = [float(o.failure_rate) for o in obs]

        capabilities: dict[str, int] = {}
        error_types: dict[str, int] = {}
        hours: set[int] = set()
        for o in obs:
            for cap in o.capabilities_used:
                capabilities[cap] = capabilities.get(cap, 0) + 1
            if o.error_type:
                error_types[o.error_type] = error_types.get(o.error_type, 0) + 1
            hours.add(o.timestamp.astimezone(timezone.utc).hour)

        baseline = BehavioralBaseline(
            agent_id=agent_id,
            observation_window_days=BASELINE_WINDOW_DAYS,
            avg_requests_per_hour=statistics.fmean(requests),
            std_dev_requests_per_hour=statistics.pstdev(requests),
            avg_failure_rate=statistics.fmean(failures),
            std_dev_failure_rate=statistics.pstdev(failures),
            typical_capabilities=capabilities,
            typical_time_windows=tuple(sorted(hours)),
            typical_error_types=error_types,
            confidence_score=min(100.0, (len(obs) / 100.0) * 100.0),
            last_updated=_now(),
            is_locked=len(obs) >= BASELINE_LOCK_OBSERVATIONS,
        )
        self._baselines[agent_id] = baseline
        return baseline

    def get_baseline(self, agent_id: str) -> BehavioralBaseline | None:
        return self._baselines.get(agent_id)

    def lock_baseline(self, agent_id: str) -> None:
        baseline = self._baselines.get(agent_id)
        if baseline is not None:
            baseline.is_locked = True


class AnomalyDetectionService:
    """Detects anomalies in a request relative to an agent's baseline."""

    def __init__(self, baseline_service: BehavioralBaselineService) -> None:
        self._baselines = baseline_service
        self._anomalies: list[AnomalyDetection] = []

    def detect(self, agent_id: str, metric: CurrentMetric) -> list[AnomalyDetection]:
        baseline = self._baselines.get_baseline(agent_id)
        if baseline is None or baseline.confidence_score < 50:
            return []

        detected: list[AnomalyDetection] = []

        if baseline.std_dev_requests_per_hour > 0:
            req_dev = (
                metric.requests_per_hour - baseline.avg_requests_per_hour
            ) / baseline.std_dev_requests_per_hour
            if abs(req_dev) > ANOMALY_THRESHOLD_SIGMA:
                detected.append(self._make(agent_id, "request_spike", abs(req_dev)))

        if baseline.std_dev_failure_rate > 0:
            fail_dev = (
                metric.failure_rate - baseline.avg_failure_rate
            ) / baseline.std_dev_failure_rate
            if abs(fail_dev) > ANOMALY_THRESHOLD_SIGMA:
                detected.append(self._make(agent_id, "failure_spike", abs(fail_dev)))

        unseen = [c for c in metric.new_capabilities if c not in baseline.typical_capabilities]
        if unseen:
            detected.append(self._make(agent_id, "new_capability_access", 3.0))

        if baseline.typical_time_windows and metric.time_of_day not in baseline.typical_time_windows:
            detected.append(self._make(agent_id, "off_hours_activity", 2.5))

        self._anomalies.extend(detected)
        return detected

    def _make(self, agent_id: str, anomaly_type: AnomalyType, deviation: float) -> AnomalyDetection:
        anomaly_score = min(100.0, deviation * 25.0)
        severity: Severity = "low"
        action: RecommendedAction = "log"
        if anomaly_score > 80:
            severity, action = "critical", "block"
        elif anomaly_score > 60:
            severity, action = "high", "quarantine"
        elif anomaly_score > 40:
            severity, action = "medium", "quarantine"

        detected_at = _now()
        return AnomalyDetection(
            detection_id=str(uuid.uuid4()),
            agent_id=agent_id,
            detected_at=detected_at,
            anomaly_type=anomaly_type,
            deviation_score=deviation,
            anomaly_score=anomaly_score,
            severity=severity,
            recommended_action=action,
            evidence_hash=sha256_json(
                {"agent_id": agent_id, "anomaly_type": anomaly_type, "ts": detected_at.isoformat()}
            ),
        )

    def for_agent(self, agent_id: str) -> list[AnomalyDetection]:
        return [a for a in self._anomalies if a.agent_id == agent_id]


@dataclass
class QuarantinedRequest:
    quarantine_id: str
    original_request: dict
    original_timestamp: datetime
    quarantine_reason: str
    anomalies_detected: list[AnomalyDetection]
    approval_required: bool
    approvers_required: int
    approval_deadline: datetime
    approvals_received: list[str] = field(default_factory=list)
    trust_suppression_applied: bool = False
    suppressed_trust_score: float = 0.0
    status: QuarantineStatus = "quarantined"
    resolution_timestamp: datetime | None = None
    resolution_reason: str | None = None


class ApproverTrustError(ValueError):
    """Raised when an approver's trust score is below the minimum threshold."""


class RequestQuarantineService:
    """Holds suspicious requests pending an M-of-N approval quorum."""

    def __init__(
        self,
        *,
        approvers_required: int = DEFAULT_APPROVERS_REQUIRED,
        hold_seconds: int = QUARANTINE_HOLD_SECONDS,
    ) -> None:
        self._quarantined: dict[str, QuarantinedRequest] = {}
        self._approvers_required = approvers_required
        self._hold_seconds = hold_seconds

    def quarantine(
        self,
        request: dict,
        anomalies: list[AnomalyDetection],
        *,
        trust_suppression: tuple[bool, float] | None = None,
    ) -> QuarantinedRequest:
        suppressed, suppressed_score = trust_suppression or (False, 0.0)
        qr = QuarantinedRequest(
            quarantine_id=str(uuid.uuid4()),
            original_request=request,
            original_timestamp=_now(),
            quarantine_reason="Anomalies detected: "
            + ", ".join(a.anomaly_type for a in anomalies),
            anomalies_detected=list(anomalies),
            approval_required=any(a.severity in ("high", "critical") for a in anomalies),
            approvers_required=self._approvers_required,
            approval_deadline=_now() + timedelta(seconds=self._hold_seconds),
            trust_suppression_applied=suppressed,
            suppressed_trust_score=suppressed_score,
        )
        self._quarantined[qr.quarantine_id] = qr
        return qr

    def get(self, quarantine_id: str) -> QuarantinedRequest | None:
        return self._quarantined.get(quarantine_id)

    def approve(self, quarantine_id: str, approver_id: str, *, approver_trust: float) -> bool:
        qr = self._quarantined.get(quarantine_id)
        if qr is None or qr.status != "quarantined":
            return False
        if approver_trust < APPROVER_MIN_TRUST:
            raise ApproverTrustError(
                f"approver {approver_id} trust {approver_trust} < {APPROVER_MIN_TRUST}"
            )
        if approver_id not in qr.approvals_received:
            qr.approvals_received.append(approver_id)

        if qr.approval_required and len(qr.approvals_received) >= qr.approvers_required:
            qr.status = "approved"
            qr.resolution_timestamp = _now()
            qr.resolution_reason = "Quorum of approvers reached"
            return True
        return False

    def deny(self, quarantine_id: str, reason: str) -> bool:
        qr = self._quarantined.get(quarantine_id)
        if qr is None:
            return False
        qr.status = "denied"
        qr.resolution_timestamp = _now()
        qr.resolution_reason = reason
        return True

    def process_expired(self) -> list[QuarantinedRequest]:
        now = _now()
        processed: list[QuarantinedRequest] = []
        for qr in self._quarantined.values():
            if qr.status == "quarantined" and now > qr.approval_deadline:
                qr.status = "denied"
                qr.resolution_timestamp = now
                qr.resolution_reason = "Approval deadline expired"
                processed.append(qr)
        return processed

    def all(self) -> list[QuarantinedRequest]:
        return list(self._quarantined.values())
