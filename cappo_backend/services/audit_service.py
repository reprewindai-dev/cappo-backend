"""Audit/ledger service — single emission point for governance-critical events.

Lineage seed: ``AIAuditLog`` hash chaining (migration note §6). Two differences
from the old backend:

1. **Fail-loud.** Governance-critical events (e.g. ``law0_violation``) must not be
   swallowed. ``record`` raises if persistence fails.
2. **Single boundary.** All LAW 0 / EI lifecycle events flow through here rather
   than being scattered across call sites.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.services.canonical import sha256_json

LAW0_VIOLATION = "law0_violation"


class AuditService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _latest_hash(self) -> str | None:
        row = self._db.execute(
            select(AuditEvent.log_hash).order_by(AuditEvent.created_at.desc()).limit(1)
        ).first()
        return row[0] if row else None

    def record(
        self,
        operation_type: str,
        payload: dict[str, Any],
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> AuditEvent:
        """Append a hash-chained event. Raises on failure (fail-loud)."""
        previous = self._latest_hash()
        chained = {
            "operation_type": operation_type,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "payload": payload,
            "previous_log_hash": previous,
        }
        event = AuditEvent(
            operation_type=operation_type,
            workspace_id=workspace_id,
            run_id=run_id,
            payload=payload,
            previous_log_hash=previous,
            log_hash=sha256_json(chained),
        )
        self._db.add(event)
        self._db.flush()
        return event

    def record_law0_violation(
        self,
        detail: str,
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AuditEvent:
        payload = {"detail": detail, "law0": True}
        if extra:
            payload.update(extra)
        return self.record(
            LAW0_VIOLATION, payload, workspace_id=workspace_id, run_id=run_id
        )
