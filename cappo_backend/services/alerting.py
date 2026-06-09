"""Alerting hooks for governance-critical events.

EI Plan §Rollout Phase 4 ("structured LAW 0 audit logging and alerting"). A
``law0_violation`` is not just an audit row — it must also raise an out-of-band
alert so operators can react. This module defines a small pluggable sink so the
audit service can emit alerts without binding to a specific transport (logging,
PagerDuty, Slack, …).

The default sink emits a single structured ``logging`` record at ``WARNING`` on
the ``cappo.law0`` logger, which a real deployment routes to its alerting
pipeline. Tests can substitute an in-memory sink to assert that an alert fired.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("cappo.law0")


class AlertSink(Protocol):
    """Receives an alert for a governance-critical event."""

    def __call__(
        self,
        operation_type: str,
        payload: dict[str, Any],
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None: ...


class LoggingAlertSink:
    """Default sink: emits one structured ``WARNING`` log record per alert."""

    def __call__(
        self,
        operation_type: str,
        payload: dict[str, Any],
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        logger.warning(
            "LAW0_ALERT operation_type=%s workspace_id=%s run_id=%s detail=%s",
            operation_type,
            workspace_id,
            run_id,
            payload.get("detail"),
            extra={
                "cappo_alert": True,
                "operation_type": operation_type,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "payload": payload,
            },
        )


# Module-level default. Call sites that do not inject a sink use this so the
# behaviour is consistent and a real deployment can configure the logger once.
default_alert_sink: AlertSink = LoggingAlertSink()
