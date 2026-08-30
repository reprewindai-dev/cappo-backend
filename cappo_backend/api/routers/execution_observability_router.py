"""Read-only proof surfaces for governed executions.

These endpoints never mint authority or synthesize proof. They expose only
persisted CAPPO/PGL evidence and independently ingested VNP observations that
are already bound to the requested execution.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import get_session
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.models.vnp_models import APIState, ProbeEvent
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.eee import EEEBuilder, EEEVerifier, VerificationVerdict

router = APIRouter(prefix="/v1")


class ExecutionEvidenceResponse(BaseModel):
    execution_id: str
    proof_state: str
    verification_reasons: list[str]
    eee: dict[str, Any]
    pgl: dict[str, Any]


class ExecutionMeasurementResponse(BaseModel):
    execution_id: str
    proof_state: str
    vnp_api_did: str
    provider: str | None = None
    resulting_state: dict[str, Any]
    observations: list[dict[str, Any]]
    aggregates: list[dict[str, Any]]


def _require_run(execution_id: str, request: Request, db: Session, error: str) -> GovernedRun:
    workspace_id = request.scope.get("auth_workspace")
    run = db.get(GovernedRun, execution_id)
    if run is None or not workspace_id or run.workspace_id != workspace_id:
        raise HTTPException(
            status_code=404,
            detail={"error": error, "execution_id": execution_id},
        )
    return run


def _eee_verifier(settings: Settings) -> EEEVerifier:
    """Verify the currently configured CAPPO evidence signing identity."""
    builder = EEEBuilder(
        signing_key=settings.ei_signing_key,
        issuer=settings.capability_beacon_issuer,
        kid=settings.capability_beacon_kid,
    )
    return EEEVerifier({settings.capability_beacon_kid: builder.public_key_bytes})


def _execution_probe_aggregates(probes: list[ProbeEvent]) -> list[dict[str, Any]]:
    by_region: dict[str, list[ProbeEvent]] = {}
    for probe in probes:
        by_region.setdefault(probe.region, []).append(probe)

    def percentile(values: list[int], fraction: float) -> int:
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction + 0.5)))
        return ordered[index]

    aggregates: list[dict[str, Any]] = []
    for region, region_probes in sorted(by_region.items()):
        latencies = [probe.latency_ms for probe in region_probes]
        successes = sum(1 for probe in region_probes if 200 <= probe.status_code < 300)
        throughputs = [
            int(
                ((probe.payload_json or {}).get("_signed_observation") or {}).get(
                    "throughput_rps", 0
                )
            )
            for probe in region_probes
        ]
        success_percent = successes * 100.0 / len(region_probes)
        aggregates.append(
            {
                "region": region,
                "p50_latency_ms": percentile(latencies, 0.50),
                "p95_latency_ms": percentile(latencies, 0.95),
                "p99_latency_ms": percentile(latencies, 0.99),
                "error_rate_percent": 100.0 - success_percent,
                "uptime_percent": success_percent,
                "throughput_rps": max(throughputs),
                "measured_at": max(probe.created_at for probe in region_probes).isoformat(),
            }
        )
    return aggregates


@router.get("/executions/{execution_id}/evidence", response_model=ExecutionEvidenceResponse)
def get_execution_evidence(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ExecutionEvidenceResponse:
    """Return only the persisted EEE/PGL evidence for one workspace execution."""
    run = _require_run(execution_id, request, db, "EVIDENCE_NOT_FOUND")
    event_id = (run.pgl_identity or {}).get("capi_evidence_event_id")
    event = db.get(PGLLedgerEvent, event_id) if isinstance(event_id, str) else None
    seal = event.payload.get("evidence_seal") if event is not None else None
    envelope = seal.get("eee") if isinstance(seal, dict) else None
    if (
        event is None
        or event.event_type != "capi_evidence_sealed"
        or not isinstance(envelope, dict)
    ):
        raise HTTPException(
            status_code=404,
            detail={"error": "EVIDENCE_NOT_FOUND", "execution_id": execution_id},
        )
    if envelope.get("execution_id") != execution_id:
        raise HTTPException(
            status_code=409,
            detail={"error": "EVIDENCE_EXECUTION_MISMATCH", "execution_id": execution_id},
        )

    report = _eee_verifier(settings).verify(envelope)
    proof_state = {
        VerificationVerdict.VALID: "verified",
        VerificationVerdict.VALID_WITH_UNRESOLVED_REFS: "verified_with_unresolved_refs",
        VerificationVerdict.INVALID: "failed",
        VerificationVerdict.UNSUPPORTED_VERSION: "unknown",
    }[report.verdict]
    if report.verdict in {VerificationVerdict.INVALID, VerificationVerdict.UNSUPPORTED_VERSION}:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "EVIDENCE_VERIFICATION_FAILED",
                "execution_id": execution_id,
                "proof_state": proof_state,
                "verification_reasons": report.reasons,
            },
        )

    return ExecutionEvidenceResponse(
        execution_id=execution_id,
        proof_state=proof_state,
        verification_reasons=report.reasons,
        eee=envelope,
        pgl={
            "event_id": event.event_id,
            "certificate_id": event.certificate_id,
            "event_hash": event.event_hash,
            "previous_event_hash": event.previous_event_hash,
            "persisted": True,
            "created_at": event.created_at.isoformat(),
        },
    )


@router.get(
    "/executions/{execution_id}/measurements",
    response_model=ExecutionMeasurementResponse,
)
def get_execution_measurements(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> ExecutionMeasurementResponse:
    """Return only immutable VNP probes explicitly bound to this execution."""
    run = _require_run(execution_id, request, db, "MEASUREMENT_NOT_FOUND")
    probes = (
        db.execute(
            select(ProbeEvent)
            .where(ProbeEvent.payload_json["execution_id"].as_string() == execution_id)
            .order_by(ProbeEvent.created_at.asc())
        )
        .scalars()
        .all()
    )
    if not probes:
        raise HTTPException(
            status_code=404,
            detail={"error": "MEASUREMENT_NOT_FOUND", "execution_id": execution_id},
        )

    api_ids = {probe.api_id for probe in probes}
    api = db.get(APIState, next(iter(api_ids))) if len(api_ids) == 1 else None
    if api is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "MEASUREMENT_API_AMBIGUOUS", "execution_id": execution_id},
        )

    result_state_hash = sha256_json(run.result_payload or {})
    if any(
        (probe.payload_json or {}).get("result_state_hash") != result_state_hash
        for probe in probes
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "STALE_RESULT_OBSERVATION",
                "execution_id": execution_id,
                "expected_result_state_hash": result_state_hash,
            },
        )

    return ExecutionMeasurementResponse(
        execution_id=execution_id,
        proof_state="verified",
        vnp_api_did=api.api_did,
        provider=(run.result_payload or {}).get("provider"),
        resulting_state={"hash": result_state_hash, "independently_observed": True},
        observations=[
            {
                "probe_id": str(probe.id),
                "worker_id": probe.worker_id,
                "region": probe.region,
                "latency_ms": probe.latency_ms,
                "status_code": probe.status_code,
                "signature": probe.signature,
                "payload": probe.payload_json,
                "observed_at": probe.created_at.isoformat(),
            }
            for probe in probes
        ],
        aggregates=_execution_probe_aggregates(probes),
    )
