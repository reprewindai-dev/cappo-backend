"""VNP telemetry only accepts attributable, externally signed probes."""

import pytest

from cappo_backend.services.vnp_telemetry_service import VNPTelemetryService


def test_vnp_telemetry_requires_a_configured_worker_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VNP_WORKER_SECRET", raising=False)

    with pytest.raises(ValueError, match="VNP_WORKER_SECRET"):
        VNPTelemetryService(db=object())


def test_vnp_telemetry_rejects_an_unsigned_probe() -> None:
    telemetry = VNPTelemetryService(db=object(), worker_secret="test-worker-secret")

    with pytest.raises(ValueError, match="signature"):
        telemetry.ingest_probe(
            api_did="did:vnp:api:provider-a",
            region="us-east",
            latency_ms=12,
            status_code=200,
            payload_json={"probe_id": "probe-1"},
        )
