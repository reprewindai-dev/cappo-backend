from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.api.routers.exec_router import _seal_terminal_eee
from cappo_backend.config import Settings
from cappo_backend.models.vnp_models import APIState
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.eee import EEEBuilder
from cappo_backend.services.ei_builder import Ed25519Signer, ExecutionIdentityBuilder
from cappo_backend.services.orchestrator import RunOrchestrator
from cappo_backend.services.pgl_client import PGLClient
from cappo_backend.services.vnp_telemetry_service import VNPTelemetryService


class _Executor:
    provider = "test-provider"
    model = "test-model"

    def execute(self, request: dict) -> dict:
        return {
            "response": "ok",
            "provider": self.provider,
            "model": self.model,
        }


def _orchestrator(db: Session) -> RunOrchestrator:
    local_settings = Settings(environment="test", ei_signing_key="same-key")
    return RunOrchestrator(
        db=db,
        pgl=PGLClient(db=db, settings=local_settings),
        builder=ExecutionIdentityBuilder(signer=Ed25519Signer("same-key")),
        executor=_Executor(),
        audit=AuditService(db, settings=local_settings),
        runtime_kind="test-runtime",
        runtime_instance="test-instance",
    )


def _run(db: Session) -> tuple[RunOrchestrator, dict]:
    orchestrator = _orchestrator(db)
    result = orchestrator.run_governed(
        {
            "prompt": "allowed",
            "directive": "ALLOW",
            "workspace_id": "test-workspace",
            "action": "contact.read",
            "scope": {
                "tools": ["contact.read"],
                "allowed_effects": ["contact.read"],
            },
        }
    )
    assert orchestrator.last_run is not None
    return orchestrator, result


def test_execution_evidence_route_returns_the_persisted_signed_seal(
    client: TestClient,
    db: Session,
    settings: Settings,
) -> None:
    orchestrator, result = _run(db)
    run = orchestrator.last_run
    assert run is not None
    builder = EEEBuilder(
        signing_key=settings.ei_signing_key,
        issuer=settings.capability_beacon_issuer,
        kid=settings.capability_beacon_kid,
    )
    envelope = asyncio.run(
        _seal_terminal_eee(
            orchestrator=orchestrator,
            run=run,
            result=result,
            capi_evidence={
                "evidence_id": "sha256:request",
                "data_hash": "sha256:input",
            },
            builder=builder,
        )
    )
    db.commit()

    response = client.get(f"/v1/executions/{run.run_id}/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["execution_id"] == run.run_id
    assert body["proof_state"] in {"verified", "verified_with_unresolved_refs"}
    assert body["eee"] == envelope
    assert body["pgl"]["event_id"] == run.pgl_identity["capi_evidence_event_id"]
    assert body["pgl"]["persisted"] is True


def test_execution_evidence_remains_verifiable_after_signing_key_rotation(
    client: TestClient,
    db: Session,
    settings: Settings,
) -> None:
    orchestrator, result = _run(db)
    run = orchestrator.last_run
    assert run is not None
    old_kid = "cappo-old"
    old_seed = "old-evidence-key"
    new_kid = "cappo-new"
    new_seed = "new-evidence-key"
    builder = EEEBuilder(
        signing_key=old_seed,
        issuer=settings.capability_beacon_issuer,
        kid=old_kid,
    )
    asyncio.run(
        _seal_terminal_eee(
            orchestrator=orchestrator,
            run=run,
            result=result,
            capi_evidence={"evidence_id": "sha256:request"},
            builder=builder,
        )
    )
    db.commit()

    settings.capability_beacon_keys_json = json.dumps(
        {old_kid: old_seed, new_kid: new_seed}
    )
    settings.capability_beacon_kid = new_kid

    response = client.get(f"/v1/executions/{run.run_id}/evidence")

    assert response.status_code == 200
    assert response.json()["eee"]["signatures"][0]["kid"] == old_kid
    assert response.json()["proof_state"] in {
        "verified",
        "verified_with_unresolved_refs",
    }


def test_execution_evidence_route_does_not_invent_missing_proof(
    client: TestClient,
    db: Session,
) -> None:
    orchestrator, _result = _run(db)
    run = orchestrator.last_run
    assert run is not None
    db.commit()

    response = client.get(f"/v1/executions/{run.run_id}/evidence")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "EVIDENCE_NOT_FOUND"


def test_execution_measurement_returns_only_signed_execution_bound_probe(
    client: TestClient,
    db: Session,
) -> None:
    orchestrator, _result = _run(db)
    run = orchestrator.last_run
    assert run is not None
    api = APIState(
        api_did="did:vnp:api:test-provider",
        name="test-provider",
        endpoint="https://provider.example/v1",
        version="v1",
    )
    db.add(api)
    db.flush()
    payload = {
        "execution_id": run.run_id,
        "result_state_hash": sha256_json(run.result_payload or {}),
    }
    signature = hmac.new(
        b"worker-secret",
        json.dumps(payload, sort_keys=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    VNPTelemetryService(db, worker_secret="worker-secret").ingest_probe(
        api_did=api.api_did,
        region="ca-central",
        latency_ms=17,
        status_code=200,
        worker_id="worker-1",
        signature=signature,
        payload_json=payload,
        throughput_rps=9,
    )
    db.commit()

    response = client.get(f"/v1/executions/{run.run_id}/measurements")

    assert response.status_code == 200
    body = response.json()
    assert body["execution_id"] == run.run_id
    assert body["proof_state"] == "verified"
    assert body["vnp_api_did"] == api.api_did
    assert body["resulting_state"] == {
        "hash": payload["result_state_hash"],
        "independently_observed": True,
    }
    assert body["observations"][0]["signature"] == signature
    assert body["observations"][0]["latency_ms"] == 17
    assert body["aggregates"][0]["region"] == "ca-central"


def test_execution_measurement_fails_closed_on_stale_result_observation(
    client: TestClient,
    db: Session,
) -> None:
    orchestrator, _result = _run(db)
    run = orchestrator.last_run
    assert run is not None
    api = APIState(
        api_did="did:vnp:api:test-provider-stale",
        name="test-provider-stale",
        endpoint="https://provider.example/v1",
        version="v1",
    )
    db.add(api)
    db.flush()
    payload = {
        "execution_id": run.run_id,
        "result_state_hash": "sha256:stale-result",
    }
    signature = hmac.new(
        b"worker-secret",
        json.dumps(payload, sort_keys=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    VNPTelemetryService(db, worker_secret="worker-secret").ingest_probe(
        api_did=api.api_did,
        region="ca-central",
        latency_ms=20,
        status_code=200,
        worker_id="worker-1",
        signature=signature,
        payload_json=payload,
    )
    db.commit()

    response = client.get(f"/v1/executions/{run.run_id}/measurements")

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "STALE_RESULT_OBSERVATION"
