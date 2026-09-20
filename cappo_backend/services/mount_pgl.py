"""Synchronous PGL anchoring for capability-mount lifecycle events."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import EphemeralScopedToken, Mount
from cappo_backend.capability_mount.service import AnchorResult
from cappo_backend.config import Settings, get_settings
from cappo_backend.services.audit_service import AuditService

_PGL_EVENT_TYPES = {
    "mount": "pre_execution_authorization",
    "action_decision": "pre_execution_authorization",
    "execution": "post_execution_attestation",
    "terminate": "custom",
}


class AuditPGLAnchor:
    """Stage local evidence and synchronously confirm one external PGL append.

    Transaction ownership stays with the mount registry. This helper must never
    commit or roll back the caller's request transaction because the registry may
    be holding a ``SELECT ... FOR UPDATE`` lock until nonce/state persistence.
    """

    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def anchor(
        self,
        event_type: str,
        *,
        action: str,
        decision: str,
        reason: str,
        mount: Mount | None,
        token: EphemeralScopedToken | None,
        **kwargs: Any,
    ) -> AnchorResult:
        payload: dict[str, Any] = {
            "event_type": event_type,
            "action": action,
            "decision": decision,
            "reason": reason,
            "mount_id": mount.id if mount else None,
            "package_ref": mount.package_ref if mount else None,
            "execution_id": token.execution_id if token else None,
        }
        try:
            event = AuditService(self.db, settings=self.settings).record(
                f"capability_mount_{event_type}",
                payload,
                workspace_id=mount.scope.workspace if mount else None,
                run_id=token.execution_id if token else None,
                forward_to_gnomledger=False,
            )
        except Exception:
            return AnchorResult("failed", detail="local PGL append failed")

        url = self.settings.pgl_ledger_url
        if not url:
            return AnchorResult(
                "pending_reconciliation",
                anchor_id=event.log_hash,
                detail="external PGL is not configured",
            )
        if not self.settings.pgl_ledger_agent_id:
            return AnchorResult(
                "pending_reconciliation",
                anchor_id=event.log_hash,
                detail="PGL_LEDGER_AGENT_ID is not configured",
            )

        try:
            headers: dict[str, str] = {}
            if self.settings.pgl_ledger_api_key:
                headers["x-api-key"] = self.settings.pgl_ledger_api_key
            response = httpx.post(
                f"{url.rstrip('/')}/api/v1/ledger/events",
                headers=headers,
                json={
                    "agent_id": self.settings.pgl_ledger_agent_id,
                    "event_type": _PGL_EVENT_TYPES.get(event_type, "custom"),
                    "actor": "cappo-backend",
                    "summary": f"Capability mount {event_type}",
                    "details": payload | {"log_hash": event.log_hash},
                    "idempotency_key": event.log_hash,
                },
                timeout=self.settings.pgl_ledger_timeout_ms / 1000,
            )
            if not 200 <= response.status_code < 300:
                response.raise_for_status()
        except Exception:
            return AnchorResult(
                "pending_reconciliation",
                anchor_id=event.log_hash,
                detail="external PGL append unconfirmed",
            )

        try:
            body = response.json()
        except (TypeError, ValueError):
            return AnchorResult(
                "pending_reconciliation",
                anchor_id=event.log_hash,
                detail="external PGL response lacked event_hash",
            )

        if not isinstance(body, dict) or body.get("persisted") is not True:
            return AnchorResult(
                "pending_reconciliation",
                anchor_id=event.log_hash,
                detail="external PGL response lacked event_hash",
            )
        event_hash = body.get("event_hash")
        if not isinstance(event_hash, str) or not event_hash.strip():
            return AnchorResult(
                "pending_reconciliation",
                anchor_id=event.log_hash,
                detail="external PGL response lacked event_hash",
            )
        return AnchorResult(
            "confirmed",
            anchor_id=event.log_hash,
            external_ref=event_hash,
        )
