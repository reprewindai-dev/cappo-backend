"""VNP Incident Service — incident tracking and management.

Monitors telemetry for anomalies and opens/closes incidents.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from cappo_backend.models.vnp_models import VNPIncident


class VNPIncidentService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def open_incident(
        self,
        api_id: uuid.UUID,
        incident_type: str,
        region: str | None = None,
        description: str | None = None
    ) -> VNPIncident:
        incident = VNPIncident(
            api_id=api_id,
            region=region,
            incident_type=incident_type,
            description=description,
            status="Open"
        )
        self._db.add(incident)
        self._db.flush()
        return incident

    def close_incident(self, incident_id: uuid.UUID) -> VNPIncident | None:
        incident = self._db.get(VNPIncident, incident_id)
        if incident:
            incident.status = "Closed"
            incident.closed_at = datetime.now(timezone.utc)
            self._db.flush()
        return incident
