"""VNP Router — trust and routing fabric API endpoints.

Exposes the VNP metrics, registry, proxy gateway, and leaderboard.
"""

from __future__ import annotations

import uuid
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.vnp_models import (
    APIState,
    PerformanceLeaderboard,
    ProbeEvent,
    RegionalTelemetry,
    RouteSnapshot,
    VNPIncident,
    VNPValidator,
)
from cappo_backend.services.vnp_proxy_service import VNPProxyService
from cappo_backend.services.vnp_telemetry_service import VNPTelemetryService

router = APIRouter(prefix="/v1/vnp", tags=["VNP Protocol"])


VNP_VERIFICATION_STACK = [
    {"section": "Physical measurements", "status": "Connected", "backend": "VEKLOM-BYOS-backend"},
    {"section": "Signed telemetry", "status": "Connected", "backend": "VEKLOM-BYOS-backend"},
    {"section": "Route beacons", "status": "Connected", "backend": "VEKLOM-BYOS-backend"},
    {"section": "Robust scoring", "status": "Connected", "backend": "VEKLOM-BYOS-backend"},
    {"section": "x402 settlement evidence", "status": "Live", "backend": "VEKLOM-BYOS-backend"},
    {"section": "PGL audit trails", "status": "Live", "backend": "cappo-backend"},
    {"section": "Agent/runtime enforcement", "status": "Live", "backend": "cappo-backend"},
]

CANONICAL_VNP_REGIONS = ["us-east", "us-west", "eu-west", "ap-southeast", "ap-northeast"]


@router.get("/methodology")
async def get_vnp_methodology() -> dict[str, Any]:
    """CAPPO-backed VNP v1.0 runtime enforcement manifest."""
    return {
        "methodology": "VNP Methodology v1.0",
        "tagline": "Cryptographic API telemetry for the machine-to-machine economy",
        "repo": "reprewindai-dev/cappo-backend",
        "verification_stack": VNP_VERIFICATION_STACK,
        "runtime": {
            "status": "Live",
            "endpoint": "/v1/exec",
            "execution_identity": "ExecutionIdentityV1",
            "pgl_certificates": "Live",
            "law0_enforcement": "Live",
        },
    }


@router.get("/metrics")
async def get_vnp_metrics(db: Session = Depends(get_session)) -> dict[str, Any]:
    """Unified live metrics query backed by stored VNP registry and telemetry."""
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        apis = db.execute(select(APIState).order_by(APIState.api_did.asc())).scalars().all()
        telemetry_rows = db.execute(
            select(RegionalTelemetry).order_by(
                RegionalTelemetry.region.asc(),
                RegionalTelemetry.measured_at.desc(),
            )
        ).scalars().all()
        probe_count = len(db.execute(select(ProbeEvent.id)).scalars().all())
    except SQLAlchemyError:
        db.rollback()
        return {
            "timestamp": timestamp,
            "protocolVersion": "VNP Methodology v1.0",
            "proofState": "degraded",
            "proofSignal": "VNP telemetry store unavailable",
            "trustBeaconMerkle": None,
            "apis": [],
            "activeNodesCount": 0,
            "expectedNodesCount": len(CANONICAL_VNP_REGIONS),
            "nodesDistribution": {region: 0 for region in CANONICAL_VNP_REGIONS},
            "total_probes_recorded": 0,
            "total_slashed_minor": 0,
            "active_validators": 0,
            "avg_composite_score": 0,
        }

    telemetry_by_api: dict[uuid.UUID, list[RegionalTelemetry]] = {}
    region_api_ids: dict[str, set[uuid.UUID]] = {}
    for row in telemetry_rows:
        telemetry_by_api.setdefault(row.api_id, []).append(row)
        region_api_ids.setdefault(row.region, set()).add(row.api_id)

    api_list = []
    for api in apis:
        regions = telemetry_by_api.get(api.id, [])

        region_map = {
            r.region: {
                "p50": r.p50_latency_ms,
                "p95": r.p95_latency_ms,
                "p99": r.p99_latency_ms,
                "errorRate": float(r.error_rate_percent),
                "uptime": float(r.uptime_percent),
                "throughput": r.throughput_rps,
                "measuredAt": r.measured_at.isoformat(),
            }
            for r in regions
        }

        api_list.append({
            "id": api.api_did,
            "name": api.name,
            "endpoint": api.endpoint,
            "version": api.version,
            "compositeScore": float(api.composite_score),
            "x402Ready": api.x402_compliant,
            "stabilityRating": api.stability_rating,
            "lastMeasured": api.last_measured.isoformat(),
            "regions": region_map,
        })

    nodes_distribution = {
        region: len(region_api_ids.get(region, set()))
        for region in CANONICAL_VNP_REGIONS
    }
    active_node_count = sum(1 for count in nodes_distribution.values() if count > 0)
    avg_composite_score = (
        round(sum(float(api.composite_score) for api in apis) / len(apis), 2)
        if apis
        else 0
    )
    proof_state = "verified" if telemetry_rows else "needs_proof"
    proof_signal = (
        f"{len(telemetry_rows)} regional telemetry rows and {probe_count} probe events recorded"
        if telemetry_rows
        else "No VNP regional telemetry rows recorded"
    )
    evidence_payload = {
        "apis": api_list,
        "nodesDistribution": nodes_distribution,
        "probeCount": probe_count,
    }
    trust_beacon = (
        "sha256:" + hashlib.sha256(
            json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if api_list or telemetry_rows or probe_count
        else None
    )

    return {
        "timestamp": timestamp,
        "protocolVersion": "VNP Methodology v1.0",
        "proofState": proof_state,
        "proofSignal": proof_signal,
        "trustBeaconMerkle": trust_beacon,
        "apis": api_list,
        "activeNodesCount": active_node_count,
        "expectedNodesCount": len(CANONICAL_VNP_REGIONS),
        "nodesDistribution": nodes_distribution,
        "total_probes_recorded": probe_count,
        "total_slashed_minor": 0,
        "active_validators": 0,
        "avg_composite_score": avg_composite_score,
    }


@router.post("/apis")
async def register_vnp_api(
    request: dict[str, Any],
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Register a new live monitored API node."""
    name = request.get("name")
    endpoint = request.get("endpoint")
    version = request.get("version", "v1.0.0")
    x402_compliant = request.get("x402Ready", False)

    if not name or not endpoint:
        raise HTTPException(status_code=400, detail="Missing required properties 'name' or 'endpoint'.")

    api_did = f"did:vnp:api:{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:4]}"

    api = APIState(
        api_did=api_did,
        name=name,
        endpoint=endpoint,
        version=version,
        x402_compliant=x402_compliant,
        composite_score=0.0,
        stability_rating="Analyzing"
    )
    db.add(api)
    db.flush()

    # Initial telemetry seeding
    telemetry_service = VNPTelemetryService(db)
    telemetry_service.ingest_probe(api_did, "us-east", 100, 200)

    return {
        "id": api_did,
        "name": api.name,
        "endpoint": api.endpoint,
        "version": api.version,
        "compositeScore": float(api.composite_score)
    }


@router.post("/proxy/{api_did}")
async def vnp_proxy_gateway(
    api_did: str,
    request: dict[str, Any],
    x_vnp_tenant: str | None = Header(None),
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Secure tunnel proxy gateway entry point."""
    telemetry_service = VNPTelemetryService(db)
    proxy_service = VNPProxyService(db, telemetry_service)

    payload = request.get("payload", {})
    tenant_name = x_vnp_tenant or "Global Public Tenant"

    try:
        result = await proxy_service.proxy_request(
            api_did=api_did,
            payload=payload,
            tenant_name=tenant_name
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/leaderboard")
async def get_vnp_leaderboard(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get the current API performance rankings."""
    rankings = db.execute(
        select(PerformanceLeaderboard)
        .order_by(PerformanceLeaderboard.rank_index.asc())
    ).scalars().all()

    return [
        {
            "rank": r.rank_index,
            "api_did": r.api.api_did,
            "name": r.api.name,
            "composite_score": float(r.monthly_composite_score),
            "is_champion": r.is_active_champion,
            "best_region": r.best_performing_region
        } for r in rankings
    ]


@router.get("/validators")
async def get_vnp_validators(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get the list of registered validators."""
    validators = db.execute(select(VNPValidator)).scalars().all()
    return [
        {
            "id": str(v.id),
            "name": v.name,
            "did": v.did,
            "stake_amount": float(v.stake_amount),
            "status": v.status
        } for v in validators
    ]


@router.get("/incidents")
async def get_vnp_incidents(
    status: str | None = None,
    db: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    """Get the list of protocol incidents."""
    stmt = select(VNPIncident)
    if status:
        stmt = stmt.where(VNPIncident.status == status)

    incidents = db.execute(stmt).scalars().all()
    return [
        {
            "id": str(i.id),
            "api_did": i.api.api_did,
            "region": i.region,
            "type": i.incident_type,
            "status": i.status,
            "description": i.description,
            "opened_at": i.opened_at.isoformat()
        } for i in incidents
    ]


@router.get("/beacon/routes")
async def get_route_beacon(
    region: str,
    policy: str = "default",
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Get the current route recommendations for a region."""
    snapshot = db.execute(
        select(RouteSnapshot)
        .where(RouteSnapshot.region == region)
        .where(RouteSnapshot.policy_name == policy)
        .order_by(RouteSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot found for this region/policy")

    return {
        "region": snapshot.region,
        "policy": snapshot.policy_name,
        "recommendations": snapshot.recommendations_json,
        "timestamp": snapshot.created_at.isoformat()
    }
