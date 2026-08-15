"""Public Interlink ingress must not become a second execution gateway."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_public_interlink_proxy_is_terminally_retired(client: TestClient) -> None:
    response = client.post(
        "/api/interlink/arbitrary/provider/path",
        headers={
            "X-Agent-ID": "agent-1",
            "X-Capability-ID": "network.egress",
            "X-Target-URL": "https://example.invalid",
            "X-Execution-Identity": "{}",
        },
        content=b'{"would":"have been forwarded"}',
    )

    assert response.status_code == 410
    assert response.json()["detail"] == "Execution is governed exclusively by POST /v1/exec"
