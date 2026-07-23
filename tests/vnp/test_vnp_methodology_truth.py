"""Truth-label guards for CAPPO's VNP methodology manifest."""

from fastapi.testclient import TestClient


def test_vnp_methodology_uses_connected_for_auth_gated_runtime(client: TestClient) -> None:
    response = client.get("/v1/vnp/methodology")
    assert response.status_code == 200

    data = response.json()
    sections = {
        item["section"]: item["status"]
        for item in data["verification_stack"]
    }

    assert data["methodology"] == "VNP Methodology v1.0"
    assert data["tagline"] == "Cryptographic API telemetry for the machine-to-machine economy"
    assert sections["x402 settlement evidence"] == "Live"
    assert sections["PGL audit trails"] == "Connected"
    assert sections["Agent/runtime enforcement"] == "Connected"
    assert data["runtime"]["status"] == "Connected"
    assert data["runtime"]["access"] == "Auth Required"
    assert data["runtime"]["pgl_certificates"] == "Connected"
    assert data["runtime"]["law0_enforcement"] == "Connected"
