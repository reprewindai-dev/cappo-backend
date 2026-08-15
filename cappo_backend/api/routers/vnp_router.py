"""VNP Router — trust and routing fabric API endpoints.

Exposes the VNP metrics, registry, proxy gateway, and leaderboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.models.vnp_models import (
    APIState,
    PerformanceLeaderboard,
    ProbeEvent,
    RegionalTelemetry,
    RouteSnapshot,
    VNPIncident,
    VNPValidator,
)
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment
from cappo_backend.services.vnp_proxy_service import VNPProxyService
from cappo_backend.services.vnp_telemetry_service import VNPTelemetryService

router = APIRouter(prefix="/v1/vnp", tags=["VNP Protocol"])


class VNPProxyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @field_validator("payload")
    @classmethod
    def bound_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value).encode("utf-8")) > 64 * 1024:
            raise ValueError("VNP proxy payload exceeds the 64 KiB limit")
        return value


class VNPApiRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    endpoint: str = Field(min_length=1, max_length=2048)
    version: str = Field(default="v1.0.0", min_length=1, max_length=50)
    x402Ready: bool = False


CANONICAL_VNP_REGIONS = ["us-east", "us-west", "eu-west", "ap-southeast", "ap-northeast"]


@router.get("/methodology")
async def get_vnp_methodology(db: Session = Depends(get_session)) -> dict[str, Any]:
    """Evidence-labelled VNP runtime manifest; never synthesize connectivity."""
    probe_count = len(db.execute(select(ProbeEvent.id)).scalars().all())
    telemetry_count = len(db.execute(select(RegionalTelemetry.id)).scalars().all())
    route_count = len(db.execute(select(RouteSnapshot.id)).scalars().all())
    pgl_certificate_count = len(db.execute(select(PGLCertificate.certificate_id)).scalars().all())
    pgl_event_count = len(db.execute(select(PGLLedgerEvent.event_id)).scalars().all())
    payment_count = len(db.execute(select(X402ConsumedPayment.tx_hash)).scalars().all())
    measured = probe_count > 0 and telemetry_count > 0
    pgl_sealed = pgl_certificate_count > 0 and pgl_event_count > 0
    verification_stack = [
        {"section": "Physical measurements", "status": "VERIFIED_LIVE" if probe_count else "UNVERIFIED", "backend": "VNP probe store"},
        {"section": "Signed telemetry", "status": "VERIFIED_LIVE" if measured else "UNVERIFIED", "backend": "VNP telemetry store"},
        {"section": "Route beacons", "status": "VERIFIED_LIVE" if route_count else "UNVERIFIED", "backend": "VNP route snapshots"},
        {"section": "Robust scoring", "status": "VERIFIED_LIVE" if measured else "UNVERIFIED", "backend": "VNP telemetry store"},
        {"section": "x402 settlement evidence", "status": "VERIFIED_LIVE" if payment_count else "UNVERIFIED", "backend": "CAPPO x402 payment store"},
        {"section": "PGL audit trails", "status": "VERIFIED_LIVE" if pgl_sealed else "UNVERIFIED", "backend": "CAPPO PGL ledger"},
        {"section": "Agent/runtime enforcement", "status": "CONFIGURED", "backend": "CAPPO /v1/exec"},
    ]
    return {
        "methodology": "VNP Methodology v1.0",
        "tagline": "Cryptographic API telemetry for the machine-to-machine economy",
        "repo": "reprewindai-dev/cappo-backend",
        "verification_stack": verification_stack,
        "runtime": {
            "status": "VERIFIED_LIVE" if pgl_sealed else "NEEDS_PROOF",
            "access": "Auth Required",
            "endpoint": "/v1/exec",
            "execution_identity": "ExecutionIdentityV1",
            "pgl_certificates": "VERIFIED_LIVE" if pgl_sealed else "UNVERIFIED",
            "law0_enforcement": "CONFIGURED",
        },
        "evidence_counts": {
            "probe_events": probe_count,
            "regional_telemetry": telemetry_count,
            "route_snapshots": route_count,
            "pgl_certificates": pgl_certificate_count,
            "pgl_events": pgl_event_count,
            "x402_payments": payment_count,
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
    trust_beacon = None

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
    request: VNPApiRegistrationRequest,
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Register a new live monitored API node."""
    name = request.name
    endpoint = request.endpoint
    version = request.version
    x402_compliant = request.x402Ready

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

    return {
        "id": api_did,
        "name": api.name,
        "endpoint": api.endpoint,
        "version": api.version,
        "compositeScore": float(api.composite_score),
        "proofState": "unmeasured",
        "proofSignal": "No signed telemetry has been received for this API",
    }


@router.post("/proxy/{api_did}")
async def vnp_proxy_gateway(
    api_did: str,
    request: VNPProxyRequest,
    x_vnp_tenant: str = Header(..., min_length=1, max_length=100),
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Secure tunnel proxy gateway entry point."""
    telemetry_service = VNPTelemetryService(db)
    proxy_service = VNPProxyService(db, telemetry_service)

    tenant_name = x_vnp_tenant

    try:
        result = await proxy_service.proxy_request(
            api_did=api_did,
            payload=request.payload,
            tenant_name=tenant_name
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="VNP proxy dependency failed")


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
