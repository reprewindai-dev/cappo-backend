"""Synchronous PGL anchoring for capability-mount lifecycle events."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import EphemeralScopedToken, Mount
from cappo_backend.capability_mount.service import AnchorResult
from cappo_backend.config import Settings, get_settings
from cappo_backend.services.audit_service import AuditService


class AuditPGLAnchor:
    """Persist locally and synchronously confirm an external PGL append when configured."""

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
            )
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            return AnchorResult("failed", detail=f"local PGL append failed: {exc}")

        url = self.settings.pgl_ledger_url
        if not url:
            return AnchorResult(
                "unconfirmed", anchor_id=event.log_hash, detail="external PGL is not configured"
            )
        try:
            headers: dict[str, str] = {}
            if self.settings.pgl_ledger_api_key:
                headers["x-api-key"] = self.settings.pgl_ledger_api_key
            response = httpx.post(
                f"{url.rstrip('/')}/api/v1/ledger/events",
                headers=headers,
                json={
                    "agent_id": token.execution_id if token else "cappo-system",
                    "event_type": event_type,
                    "actor": "cappo-backend",
                    "summary": f"Capability mount {event_type}",
                    "details": payload | {"log_hash": event.log_hash},
                },
                timeout=self.settings.pgl_ledger_timeout_ms / 1000,
            )
            response.raise_for_status()
        except Exception:
            return AnchorResult(
                "unconfirmed", anchor_id=event.log_hash, detail="external PGL append unconfirmed"
            )
        return AnchorResult("confirmed", anchor_id=event.log_hash)
