import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from cappo_backend.models.mcpapi_v2 import (
    AnomalyDetection,
    AnomalyType,
    ApprovalQuorum,
    BehavioralBaseline,
    CurrentMetric,
    QuarantinedRequest,
    QuarantineStatus,
    RecommendedAction,
    Severity,
)

# ============================================================================
# BEHAVIORAL BASELINE SERVICE
# ============================================================================

class BehavioralBaselineService:
    def __init__(self):
        self.baselines: Dict[str, BehavioralBaseline] = {}
        self.observations: Dict[str, List[Dict[str, Any]]] = {}
        self.BASELINE_LOCK_DAYS = 30

    def get_baseline(self, agent_id: str) -> Optional[BehavioralBaseline]:
        return self.baselines.get(agent_id)

    # Note: Full statistical baseline building logic omitted for brevity, 
    # assumes pre-populated or handled asynchronously in production.

# ============================================================================
# ANOMALY DETECTION SERVICE
# ============================================================================

class AnomalyDetectionService:
    def __init__(self, baseline_service: BehavioralBaselineService):
        self.baseline_service = baseline_service
        self.anomalies: List[AnomalyDetection] = []
        self.ANOMALY_THRESHOLD = 2.0

    def detect_anomalies(self, agent_id: str, current_metric: CurrentMetric) -> List[AnomalyDetection]:
        baseline = self.baseline_service.get_baseline(agent_id)
        if not baseline or baseline.confidence_score < 50:
            return [] # Not enough data
        
        detected = []

        # 1. Request spike
        if baseline.std_dev_requests_per_hour > 0:
            req_dev = (current_metric.requests_per_hour - baseline.avg_requests_per_hour) / baseline.std_dev_requests_per_hour
            if abs(req_dev) > self.ANOMALY_THRESHOLD:
                detected.append(self._create_anomaly(agent_id, AnomalyType.REQUEST_SPIKE, current_metric, baseline, abs(req_dev)))

        # 2. New Capability
        new_caps = [cap for cap in current_metric.new_capabilities if cap not in baseline.typical_capabilities]
        if new_caps:
            detected.append(self._create_anomaly(agent_id, AnomalyType.NEW_CAPABILITY_ACCESS, current_metric, baseline, len(new_caps)))
            
        # 3. Off hours
        if current_metric.time_of_day not in baseline.typical_time_windows:
            detected.append(self._create_anomaly(agent_id, AnomalyType.OFF_HOURS_ACTIVITY, current_metric, baseline, 1.0))

        self.anomalies.extend(detected)
        return detected

    def _create_anomaly(self, agent_id: str, anomaly_type: AnomalyType, current_metric: CurrentMetric, baseline: BehavioralBaseline, deviation_score: float) -> AnomalyDetection:
        anomaly_score = min(100.0, (abs(deviation_score) / 5.0) * 100.0)
        
        severity = Severity.LOW
        action = RecommendedAction.LOG

        if anomaly_score > 80:
            severity = Severity.CRITICAL
            action = RecommendedAction.BLOCK
        elif anomaly_score > 60:
            severity = Severity.HIGH
            action = RecommendedAction.QUARANTINE
        elif anomaly_score > 40:
            severity = Severity.MEDIUM
            action = RecommendedAction.QUARANTINE

        now = datetime.now(timezone.utc)
        evidence_hash = hashlib.sha256(json.dumps({
            "agent_id": agent_id,
            "anomaly_type": anomaly_type.value,
            "timestamp": now.isoformat()
        }).encode()).hexdigest()

        return AnomalyDetection(
            detection_id=str(uuid.uuid4()),
            agent_id=agent_id,
            detected_at=now.isoformat(),
            anomaly_type=anomaly_type,
            baseline=baseline,
            current_metric=current_metric,
            deviation_score=deviation_score,
            anomaly_score=anomaly_score,
            severity=severity,
            recommended_action=action,
            evidence_hash=evidence_hash
        )

# ============================================================================
# REQUEST QUARANTINE SERVICE
# ============================================================================

class RequestQuarantineService:
    def __init__(self):
        self._lock = threading.RLock()
        self.quarantined: Dict[str, QuarantinedRequest] = {}
        self.HOLD_DURATION_MS = 60 * 60 * 1000 # 1 hour
        self.DEFAULT_APPROVERS_REQUIRED = 2

    def quarantine(
        self,
        request: Dict[str, Any],
        anomalies: List[AnomalyDetection],
        trust_suppression: Optional[Dict[str, Any]] = None,
        requester_id: Optional[str] = None,
    ) -> QuarantinedRequest:
        from cappo_backend.services.safety import extract_requester_identities

        reasons = [a.anomaly_type.value for a in anomalies]
        approval_required = any(a.severity in [Severity.HIGH, Severity.CRITICAL] for a in anomalies)
        bound_requester_id, bound_identities = extract_requester_identities(request, requester_id=requester_id)

        now = datetime.now(timezone.utc)
        qr = QuarantinedRequest(
            quarantine_id=str(uuid.uuid4()),
            original_request=request,
            original_timestamp=now.isoformat(),
            quarantine_reason=f"Anomalies detected: {', '.join(reasons)}",
            anomalies_detected=anomalies,
            trust_suppression_applied=trust_suppression.get("applied", False) if trust_suppression else False,
            suppressed_trust_score=trust_suppression.get("suppressed_score", 0) if trust_suppression else 0,
            approval_required=approval_required,
            approvers_required=self.DEFAULT_APPROVERS_REQUIRED,
            approvals_received=[],
            approval_deadline=(now + timedelta(hours=1)).isoformat(),
            status=QuarantineStatus.QUARANTINED,
            requester_id=bound_requester_id,
            bound_identities=bound_identities,
        )
        with self._lock:
            self.quarantined[qr.quarantine_id] = qr
        return qr

    def get_quarantined(self, quarantine_id: str) -> Optional[QuarantinedRequest]:
        with self._lock:
            return self.quarantined.get(quarantine_id)

    def approve(
        self,
        quarantine_id: str,
        approver_id: str,
        *,
        approver_trust: float = 100.0,
        authenticated_approver_id: Optional[str] = None,
    ) -> bool:
        from cappo_backend.services.safety import (
            ApproverTrustError,
            SelfApprovalForbiddenError,
            extract_leaf_identity,
            extract_requester_identities,
            normalize_identity,
        )

        with self._lock:
            qr = self.quarantined.get(quarantine_id)
            if qr is None or qr.status != QuarantineStatus.QUARANTINED:
                return False

            effective_approver = (
                str(authenticated_approver_id).strip()
                if authenticated_approver_id and str(authenticated_approver_id).strip()
                else (str(approver_id).strip() if approver_id and str(approver_id).strip() else None)
            )
            if not effective_approver or not normalize_identity(effective_approver):
                raise ValueError("Approver identity is required and cannot be blank")

            clean_approver = str(approver_id).strip() if approver_id else effective_approver

            # Assemble all bound requester & delegation chain identities
            all_bound: set[str] = set()
            for b in getattr(qr, "bound_identities", []):
                norm = normalize_identity(b)
                if norm:
                    all_bound.add(norm)
            if qr.requester_id:
                norm = normalize_identity(qr.requester_id)
                if norm:
                    all_bound.add(norm)
            _, fallback_idents = extract_requester_identities(qr.original_request)
            for fb in fallback_idents:
                norm = normalize_identity(fb)
                if norm:
                    all_bound.add(norm)

            # Enforce Requester/Approver Separation against both authoritative and claimed identities
            check_identities = [effective_approver]
            if clean_approver and clean_approver != effective_approver:
                check_identities.append(clean_approver)

            # Expand check_identities with leaf identities if present (SPIFFE, URN, scoped)
            expanded_checks: list[str] = []
            for ident in check_identities:
                if ident not in expanded_checks:
                    expanded_checks.append(ident)
                leaf = extract_leaf_identity(ident)
                if leaf and leaf not in expanded_checks:
                    expanded_checks.append(leaf)

            for ident in expanded_checks:
                if normalize_identity(ident) in all_bound:
                    raise SelfApprovalForbiddenError(
                        f"SELF_APPROVAL_FORBIDDEN: requester identity '{ident}' cannot approve own request (bound identities={sorted(all_bound)})",
                        requester_id=qr.requester_id or ident,
                        approver_id=ident,
                        decision="DENY",
                        denial_reason="SELF_APPROVAL_FORBIDDEN",
                    )

            if approver_trust < 80.0:
                raise ApproverTrustError(f"approver {effective_approver} trust {approver_trust} < 80.0")

            recorded_approver = effective_approver
            # Case-insensitive and whitespace/unicode-normalized deduplication to prevent M-of-N quorum bypass
            if not any(normalize_identity(existing) == normalize_identity(recorded_approver) for existing in qr.approvals_received):
                qr.approvals_received.append(recorded_approver)

            if qr.approval_required and len(qr.approvals_received) >= qr.approvers_required:
                qr.status = QuarantineStatus.APPROVED
                qr.resolution_timestamp = datetime.now(timezone.utc).isoformat()
                qr.resolution_reason = "Quorum of approvers reached"
                return True
            return False

    def deny(self, quarantine_id: str, reason: str) -> bool:
        with self._lock:
            qr = self.quarantined.get(quarantine_id)
            if qr is None:
                return False
            qr.status = QuarantineStatus.DENIED
            qr.resolution_timestamp = datetime.now(timezone.utc).isoformat()
            qr.resolution_reason = reason
            return True

# ============================================================================
# APPROVAL QUORUM SERVICE
# ============================================================================

class ApprovalQuorumService:
    def __init__(self):
        self.quorums: Dict[str, ApprovalQuorum] = {}
        self.DEFAULT_QUORUM_SIZE = 2
        self.TRUST_THRESHOLD = 80

    def create_quorum(self, quarantine_id: str, required_approvers: List[str], required_count: int = 2, escalation_path: List[str] = None) -> ApprovalQuorum:
        quorum = ApprovalQuorum(
            approval_id=str(uuid.uuid4()),
            quarantine_id=quarantine_id,
            required_approvers=required_approvers,
            current_approvals={},
            required_count=required_count,
            threshold_reached=False,
            approval_deadline=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            escalation_path=escalation_path or [],
            escalation_triggered=False
        )
        self.quorums[quorum.approval_id] = quorum
        return quorum
