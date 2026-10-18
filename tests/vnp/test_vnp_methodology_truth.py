"""Truth-label guards for CAPPO's VNP methodology manifest."""

from fastapi.testclient import TestClient


def test_vnp_methodology_does_not_claim_runtime_evidence_without_records(client: TestClient) -> None:
    response = client.get("/v1/vnp/methodology")
    assert response.status_code == 200

    data = response.json()
    sections = {
        item["section"]: item["status"]
        for item in data["verification_stack"]
    }

    assert data["methodology"] == "VNP Methodology v1.0"
    assert data["tagline"] == "Cryptographic API telemetry for the machine-to-machine economy"
    assert sections["x402 settlement evidence"] == "UNVERIFIED"
    assert sections["PGL audit trails"] == "UNVERIFIED"
    assert sections["Agent/runtime enforcement"] == "CONFIGURED"
    assert data["runtime"]["status"] == "NEEDS_PROOF"
    assert data["runtime"]["access"] == "Auth Required"
    assert data["runtime"]["pgl_certificates"] == "UNVERIFIED"
    assert data["runtime"]["law0_enforcement"] == "CONFIGURED"
