"""VNP Router — trust and routing fabric API endpoints.

Exposes the VNP metrics, registry, proxy gateway, and leaderboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.vnp_models import (
    APIState,
    PerformanceLeaderboard,
    RegionalTelemetry,
    RouteSnapshot,
    VNPIncident,
    VNPValidator,
)
from cappo_backend.services.vnp_proxy_service import VNPProxyService
from cappo_backend.services.vnp_telemetry_service import VNPTelemetryService

router = APIRouter(prefix="/v1/vnp", tags=["VNP Protocol"])


@router.get("/metrics")
async def get_vnp_metrics(db: Session = Depends(get_session)) -> dict[str, Any]:
    """Unified Real-Time Live metrics query."""
    apis = db.execute(select(APIState)).scalars().all()

    api_list = []
    for api in apis:
        regions = db.execute(
            select(RegionalTelemetry).where(RegionalTelemetry.api_id == api.id)
        ).scalars().all()

        region_map = {
            r.region: {
                "p50": r.p50_latency_ms,
                "p95": r.p95_latency_ms,
                "p99": r.p99_latency_ms,
                "errorRate": float(r.error_rate_percent),
                "uptime": float(r.uptime_percent),
                "throughput": r.throughput_rps
            } for r in regions
        }

        api_list.append({
            "id": api.api_did,
            "name": api.name,
            "endpoint": api.endpoint,
            "version": api.version,
            "compositeScore": float(api.composite_score),
            "x402Ready": api.x402_compliant,
            "stabilityRating": api.stability_rating,
            "regions": region_map
        })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "protocolVersion": "VNP v0.1.0-Locked",
        "trustBeaconMerkle": "0x" + uuid.uuid4().hex, # Mock Merkle anchor
        "apis": api_list,
        "activeNodesCount": len(apis) + 16, # Mocking additional nodes
        "nodesDistribution": {
            "us-east": 4,
            "us-west": 3,
            "eu-west": 4,
            "ap-southeast": 2,
            "ap-northeast": 3
        }
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
