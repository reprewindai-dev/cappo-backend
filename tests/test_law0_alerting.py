"""Tests for the LAW 0 alerting hook (Phase 5).

A ``law0_violation`` must both persist an audit row (fail-loud) and raise an
out-of-band alert via the pluggable sink. Other operation types must not alert.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        operation_type: str,
        payload: dict[str, Any],
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "operation_type": operation_type,
                "payload": payload,
                "workspace_id": workspace_id,
                "run_id": run_id,
            }
        )


class TestAlertingHook:
    def test_law0_violation_fires_alert(self, db: Session) -> None:
        sink = RecordingSink()
        audit = AuditService(db, alert_sink=sink)
        audit.record_law0_violation(
            "bad identity", workspace_id="ws1", run_id="run1"
        )

        assert len(sink.calls) == 1
        call = sink.calls[0]
        assert call["operation_type"] == "law0_violation"
        assert call["payload"]["detail"] == "bad identity"
        assert call["workspace_id"] == "ws1"
        # The audit row is still persisted (fail-loud), not replaced by the alert.
        assert db.query(AuditEvent).filter(
            AuditEvent.operation_type == "law0_violation"
        ).count() == 1

    def test_non_law0_event_does_not_alert(self, db: Session) -> None:
        sink = RecordingSink()
        audit = AuditService(db, alert_sink=sink)
        audit.record("run_attested", {"ok": True})
        assert sink.calls == []

    def test_gateway_rejection_fires_alert(self, db: Session, settings) -> None:
        sink = RecordingSink()
        audit = AuditService(db, alert_sink=sink)
        gateway = MCPGateway(audit, settings=settings)

        # Empty (non-None) identity fails rule 1 inside the validated block, which
        # records a law0_violation and therefore fires the alert.
        try:
            gateway.require_execution_identity({}, action="tool", workspace_id="ws")
        except EIValidationError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("expected EIValidationError")

        assert len(sink.calls) == 1
        assert sink.calls[0]["operation_type"] == "law0_violation"
