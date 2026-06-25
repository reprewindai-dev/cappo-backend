"""Audit/ledger service — single emission point for governance-critical events.

Lineage seed: ``AIAuditLog`` hash chaining (migration note §6). Two differences
from the old backend:

1. **Fail-loud.** Governance-critical events (e.g. ``law0_violation``) must not be
   swallowed. ``record`` raises if persistence fails.
2. **Single boundary.** All LAW 0 / EI lifecycle events flow through here rather
   than being scattered across call sites.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.config import Settings, get_settings
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.services.alerting import AlertSink, default_alert_sink
from cappo_backend.services.canonical import sha256_json

logger = logging.getLogger(__name__)

LAW0_VIOLATION = "law0_violation"

# Operation types that raise an out-of-band alert in addition to being persisted.
ALERTING_OPERATIONS = frozenset({LAW0_VIOLATION})


class AuditService:
    def __init__(
        self,
        db: Session,
        *,
        alert_sink: AlertSink | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        # Pluggable alert transport (EI Plan §Phase 4 alerting). Defaults to the
        # module-level logging sink; tests inject an in-memory sink to assert.
        self._alert_sink: AlertSink = alert_sink or default_alert_sink
        self._settings = settings or get_settings()

    def _latest_hash(self) -> str | None:
        all_hashes = list(self._db.execute(select(AuditEvent.log_hash)).scalars())
        if not all_hashes:
            return None
        referenced = set(
            self._db.execute(
                select(AuditEvent.previous_log_hash).where(
                    AuditEvent.previous_log_hash.isnot(None)
                )
            )
            .scalars()
        )
        tails = [h for h in all_hashes if h not in referenced]
        return tails[0] if tails else all_hashes[-1]

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

        # Phase 7: PGL ledger forwarding (best-effort, non-blocking).
        self._forward_to_gnomledger(event)

        # Fire an out-of-band alert for governance-critical events. The audit row
        # is already persisted (fail-loud); the alert is a best-effort notify on
        # top and must never mask the recorded event.
        if operation_type in ALERTING_OPERATIONS:
            self._alert_sink(
                operation_type,
                payload,
                workspace_id=workspace_id,
                run_id=run_id,
            )

        return event

    def _forward_to_gnomledger(self, event: AuditEvent) -> None:
        """Forward event to external gnomledger if configured.

        Mirrors the cAPI Phase 7 best-effort forwarding logic.
        """
        url = self._settings.pgl_ledger_url
        if not url:
            return

        def _worker():
            try:
                # Mirror cAPI summary format
                summary = f"cappo {event.operation_type}: {event.run_id or 'no-run'}"
                body = {
                    "agent_id": event.run_id or "cappo-system",
                    "event_type": "custom",
                    "actor": event.workspace_id or "cappo-admin",
                    "summary": summary[:255],
                    "details": {
                        "source": "cappo",
                        "operation_type": event.operation_type,
                        "workspace_id": event.workspace_id,
                        "run_id": event.run_id,
                        "payload": event.payload,
                        "log_hash": event.log_hash,
                        "previous_hash": event.previous_log_hash,
                    },
                    "idempotency_key": event.log_hash,
                }
                headers = {}
                if self._settings.pgl_ledger_api_key:
                    headers["x-api-key"] = self._settings.pgl_ledger_api_key

                with httpx.Client(timeout=self._settings.pgl_ledger_timeout_ms / 1000.0) as client:
                    resp = client.post(
                        f"{url.rstrip('/')}/api/v1/ledger/events",
                        json=body,
                        headers=headers,
                    )
                    resp.raise_for_status()
            except Exception as e:
                logger.warning("Failed to forward audit event to gnomledger: %s", e)

        # Fire and forget in a background thread to keep the pipeline synchronous.
        threading.Thread(target=_worker, daemon=True).start()

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
        return self.record(LAW0_VIOLATION, payload, workspace_id=workspace_id, run_id=run_id)
