"""VNP Test Suite — verifying the trust and routing fabric."""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.models.vnp_models import APIState, ProbeEvent, RegionalTelemetry, VNPProvider


def test_vnp_control_plane_onboarding(client: TestClient, db: Session):
    # 1. Register Provider
    response = client.post("/v1/vnp/admin/providers", json={"name": "Test Cloud"})
    assert response.status_code == 200
    provider_id = response.json()["id"]

    # 2. Register API
    payload = {
        "name": "Production LLM",
        "endpoint": "https://api.testcloud.com/v1",
        "version": "v2.0",
        "x402Ready": True,
    }
    response = client.post(f"/v1/vnp/admin/providers/{provider_id}/apis", json=payload)
    assert response.status_code == 200
    api_id = response.json()["id"]
    api_did = response.json()["api_did"]

    # Verify in DB
    api = db.get(APIState, uuid.UUID(api_id))
    assert api.provider_id == uuid.UUID(provider_id)
    assert api.api_did == api_did


def test_vnp_signed_telemetry(client: TestClient, db: Session, monkeypatch):
    monkeypatch.setenv("VNP_WORKER_SECRET", "test-secret")
    # Onboard
    client.post("/v1/vnp/admin/providers", json={"name": "Telemetry Cloud"})
    provider = db.query(VNPProvider).first()
    client.post(
        f"/v1/vnp/admin/providers/{provider.id}/apis",
        json={"name": "Metrics API", "endpoint": "https://m.test"},
    )
    api = db.query(APIState).first()

    import hashlib
    import hmac

    from cappo_backend.services.vnp_telemetry_service import (
        VNPTelemetryService,
        canonical_probe_observation,
    )

    service = VNPTelemetryService(db)

    # Build probe payload and sign it as a probe worker would
    payload_json = {"sample": "data"}
    payload_str = canonical_probe_observation(payload=payload_json, worker_id="worker-001", region="eu-west", latency_ms=150, status_code=200, throughput_rps=0)
    probe_signature = hmac.new(
        b"test-secret", payload_str.encode(), hashlib.sha256
    ).hexdigest()

    # Ingest with raw event tracking
    service.ingest_probe(
        api_did=api.api_did,
        region="eu-west",
        latency_ms=150,
        status_code=200,
        worker_id="worker-001",
        payload_json=payload_json,
        signature=probe_signature,
    )
    db.commit()

    # Verify raw ProbeEvent stored
    probe = db.query(ProbeEvent).filter_by(api_id=api.id).first()
    assert probe is not None
    assert probe.worker_id == "worker-001"
    assert probe.latency_ms == 150

    # Verify aggregates
    telemetry = db.query(RegionalTelemetry).filter_by(api_id=api.id, region="eu-west").first()
    assert telemetry.p50_latency_ms == 150


def test_vnp_incidents(client: TestClient, db: Session):
    # Setup
    client.post("/v1/vnp/admin/providers", json={"name": "SLA Cloud"})
    provider = db.query(VNPProvider).first()
    client.post(
        f"/v1/vnp/admin/providers/{provider.id}/apis",
        json={"name": "SLA API", "endpoint": "https://s.test"},
    )
    api = db.query(APIState).first()

    from cappo_backend.services.vnp_incident_service import VNPIncidentService

    service = VNPIncidentService(db)

    # Open Incident
    service.open_incident(
        api_id=api.id,
        region="us-east",
        incident_type="Latency Spike",
        description="P99 exceeded 2000ms",
    )
    db.commit()

    # Verify via API
    response = client.get("/v1/vnp/incidents?status=Open")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["type"] == "Latency Spike"
    assert data[0]["api_did"] == api.api_did


def test_vnp_validators(client: TestClient, db: Session):
    from cappo_backend.services.vnp_validator_service import VNPValidatorService

    service = VNPValidatorService(db)

    service.register_validator("Hetzner Node", stake_amount=Decimal("1000.50"))
    db.commit()

    response = client.get("/v1/vnp/validators")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["name"] == "Hetzner Node"
    assert data[0]["stake_amount"] == 1000.50


def test_vnp_route_beacon_snapshots(client: TestClient, db: Session):
    # Setup API with score
    client.post("/v1/vnp/admin/providers", json={"name": "Route Cloud"})
    provider = db.query(VNPProvider).first()
    client.post(
        f"/v1/vnp/admin/providers/{provider.id}/apis",
        json={"name": "Route API", "endpoint": "https://r.test"},
    )
    api = db.query(APIState).first()
    api.composite_score = Decimal("95.50")
    db.commit()

    from cappo_backend.services.vnp_route_snapshot_service import VNPRouteSnapshotService

    service = VNPRouteSnapshotService(db)

    # Generate snapshot
    service.generate_snapshot(region="us-west")
    db.commit()

    # Query Beacon
    response = client.get("/v1/vnp/beacon/routes?region=us-west")
    assert response.status_code == 200
    data = response.json()
    assert data["region"] == "us-west"
    assert api.api_did in data["recommendations"]
    assert data["recommendations"][api.api_did] == 95.5
