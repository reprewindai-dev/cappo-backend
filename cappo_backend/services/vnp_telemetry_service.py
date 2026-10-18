"""VNP Telemetry Service — ingestion, scoring, and aggregate management.

Implements the 40/30/20/10 weighting matrix from the VNP Prototype:
- 40%: p99 Latency
- 30%: Uptime Stability
- 20%: Security / Auth Standards
- 10%: Throughput Capacity (RPS)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cappo_backend.models.vnp_models import (
    APIState,
    PerformanceLeaderboard,
    ProbeEvent,
    RegionalTelemetry,
)

logger = logging.getLogger(__name__)


class VNPTelemetryService:
    def __init__(self, db: Session, worker_secret: str | None = None) -> None:
        self._db = db
        self._worker_secret = (worker_secret or os.environ.get("VNP_WORKER_SECRET", "")).strip()
        if not self._worker_secret:
            raise ValueError("VNP_WORKER_SECRET must be configured for signed probe ingestion")

    def _verify_signature(self, payload: str, signature: str) -> bool:
        """Verify the HMAC-SHA256 signature of a probe payload."""
        expected = hmac.new(
            self._worker_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def ingest_probe(
        self,
        api_did: str,
        region: str,
        latency_ms: int,
        status_code: int,
        worker_id: str = "worker-1",
        signature: str | None = None,
        payload_json: dict[str, Any] | None = None,
        throughput_rps: int = 0
    ) -> RegionalTelemetry:
        """Ingest a single probe measurement and update aggregates."""
        if not 1 <= len(region) <= 50:
            raise ValueError("region must contain between 1 and 50 characters")
        if not 0 <= latency_ms <= 600_000:
            raise ValueError("latency_ms must be between 0 and 600000")
        if not 100 <= status_code <= 599:
            raise ValueError("status_code must be between 100 and 599")
        if not 1 <= len(worker_id) <= 100:
            raise ValueError("worker_id must contain between 1 and 100 characters")
        if not 0 <= throughput_rps <= 10_000_000:
            raise ValueError("throughput_rps must be between 0 and 10000000")

        # VNP is an observation boundary.  The application process must never
        # manufacture its own probe evidence; every stored measurement needs a
        # signature supplied by the independently operated probe worker.
        payload_str = json.dumps(payload_json or {}, sort_keys=True)
        if signature is None:
            raise ValueError("Probe signature is required")
        if not self._verify_signature(payload_str, signature):
            raise ValueError("Invalid probe signature")

        api = self._db.execute(
            select(APIState).where(APIState.api_did == api_did)
        ).scalar_one_or_none()

        if not api:
            raise ValueError(f"API with DID {api_did} not found")

        # 2. Store raw ProbeEvent (Immutable Evidence)
        probe_event = ProbeEvent(
            api_id=api.id,
            worker_id=worker_id,
            region=region,
            latency_ms=latency_ms,
            status_code=status_code,
            signature=signature,
            payload_json=payload_json or {}
        )
        self._db.add(probe_event)

        # 3. Update regional aggregates
        telemetry = self._db.execute(
            select(RegionalTelemetry)
            .where(RegionalTelemetry.api_id == api.id)
            .where(RegionalTelemetry.region == region)
            .order_by(RegionalTelemetry.measured_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        success = 200 <= status_code < 300

        if not telemetry:
            telemetry = RegionalTelemetry(
                api_id=api.id,
                region=region,
                p50_latency_ms=latency_ms,
                p95_latency_ms=int(latency_ms * 1.5),
                p99_latency_ms=int(latency_ms * 2.0),
                error_rate_percent=Decimal("0.00") if success else Decimal("100.00"),
                uptime_percent=Decimal("100.00") if success else Decimal("0.00"),
                throughput_rps=throughput_rps
            )
            self._db.add(telemetry)
        else:
            # Simple EMA-style update for the MVP
            telemetry.p50_latency_ms = int(telemetry.p50_latency_ms * 0.7 + latency_ms * 0.3)
            telemetry.p95_latency_ms = int(telemetry.p50_latency_ms * 1.5)
            telemetry.p99_latency_ms = int(telemetry.p50_latency_ms * 2.0)

            if success:
                telemetry.error_rate_percent = Decimal(max(0, float(telemetry.error_rate_percent) - 5))
                telemetry.uptime_percent = Decimal(min(100, float(telemetry.uptime_percent) + 0.5))
            else:
                telemetry.error_rate_percent = Decimal(min(100, float(telemetry.error_rate_percent) + 10))
                telemetry.uptime_percent = Decimal(max(0, float(telemetry.uptime_percent) - 2.5))

            telemetry.throughput_rps = throughput_rps
            telemetry.measured_at = func.now()

        self._db.flush()

        # Trigger score recalculation
        self.recalculate_api_score(api.id)

        return telemetry

    def recalculate_api_score(self, api_id: Any) -> Decimal:
        """Compute composite score using the 40/30/20/10 matrix."""
        regions = self._db.execute(
            select(RegionalTelemetry).where(RegionalTelemetry.api_id == api_id)
        ).scalars().all()

        if not regions:
            return Decimal("0.00")

        # Average across regions
        avg_p99 = sum(r.p99_latency_ms for r in regions) / len(regions)
        avg_uptime = sum(float(r.uptime_percent) for r in regions) / len(regions)
        avg_error = sum(float(r.error_rate_percent) for r in regions) / len(regions)

        # Scoring components (0-100)
        # Latency: p99 < 200ms = 100, 2000ms = 0
        lat_score = max(0, 100 - (avg_p99 / 20))
        # Uptime: 100% = 100, 90% = 0
        uptime_score = max(0, (avg_uptime - 90) * 10)
        # Security: placeholder for now
        security_score = 95 if avg_error < 1.0 else 80
        # Throughput: simplified
        throughput_score = 100 if any(r.throughput_rps > 1000 for r in regions) else 70

        composite = (
            Decimal(str(lat_score)) * Decimal("0.40") +
            Decimal(str(uptime_score)) * Decimal("0.30") +
            Decimal(str(security_score)) * Decimal("0.20") +
            Decimal(str(throughput_score)) * Decimal("0.10")
        )

        composite = Decimal(round(float(composite), 2))

        # Update API State
        api = self._db.get(APIState, api_id)
        if api:
            api.composite_score = composite
            api.last_measured = func.now()
            api.stability_rating = self._get_stability_rating(composite)

        self._db.flush()

        # Update Leaderboard
        self._update_leaderboard_rankings()

        return composite

    def _get_stability_rating(self, score: Decimal) -> str:
        if score >= 95:
            return "Absolute Peak"
        if score >= 90:
            return "Excellent"
        if score >= 80:
            return "Stable"
        return "Degraded"

    def _update_leaderboard_rankings(self) -> None:
        """Recompute sequential rankings based on composite scores."""
        apis = self._db.execute(
            select(APIState).order_by(APIState.composite_score.desc(), APIState.last_measured.desc())
        ).scalars().all()

        for index, api in enumerate(apis, start=1):
            lb = self._db.execute(
                select(PerformanceLeaderboard).where(PerformanceLeaderboard.api_id == api.id)
            ).scalar_one_or_none()

            if not lb:
                lb = PerformanceLeaderboard(
                    api_id=api.id,
                    monthly_composite_score=api.composite_score,
                    rank_index=index,
                    is_active_champion=(index == 1)
                )
                self._db.add(lb)
            else:
                lb.monthly_composite_score = api.composite_score
                lb.rank_index = index
                lb.is_active_champion = (index == 1)
                lb.updated_at = func.now()

        self._db.flush()
