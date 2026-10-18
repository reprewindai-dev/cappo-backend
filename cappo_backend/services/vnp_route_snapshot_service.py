"""VNP Route Snapshot Service — periodic route recommendation generation.

Computes snapshots of top-performing APIs by region and policy.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.models.vnp_models import APIState, RouteSnapshot

logger = logging.getLogger(__name__)


class VNPRouteSnapshotService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def generate_snapshot(self, region: str, policy_name: str = "default") -> RouteSnapshot:
        """Generate a new route recommendation snapshot for a region."""
        # Query top APIs in this region based on composite score (simplification)
        apis = self._db.execute(
            select(APIState)
            .order_by(APIState.composite_score.desc())
            .limit(5)
        ).scalars().all()

        recommendations = {}
        for api in apis:
            # In a real system, we'd adjust weights based on the policy
            recommendations[api.api_did] = float(api.composite_score)

        snapshot = RouteSnapshot(
            region=region,
            policy_name=policy_name,
            recommendations_json=recommendations
        )
        self._db.add(snapshot)
        self._db.flush()

        logger.info(f"Generated route snapshot for {region} ({policy_name})")
        return snapshot
