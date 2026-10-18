"""GnomledgerPGLClient must read gnomledger's agent lifecycle, not assume its own.

gnomledger creates agents with status "registered" and only ever moves them to
"decommissioned" (gnomledger ``models.Agent.status``, ``decommission_service``); its own
routes treat "active", "registered" and "standard" as live. CAPPO used to require the
literal "active", so ``validate_agent_for_execution`` refused every real agent and no
pre_execution_authorization could be written (reproduced against a live gnomledger:
"Agent agent_b340a633c4b7 is not active (status: registered)").
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from cappo_backend.config import Settings
from cappo_backend.services.gnomledger_pgl_client import GnomledgerPGLClient


def _agent_payload(status: str, trust_score: float = 50.0, risk_tier: str = "sandbox") -> dict[str, Any]:
    # Shape of gnomledger GET /api/v1/agents/{agent_id} (AgentDetailResponse).
    return {
        "agent_id": "agent_b340a633c4b7",
        "certificate_id": "cert_807d564327a0",
        "name": "capos-e2e-agent",
        "creator": "api_key:1:capability-os-runtime",
        "jurisdiction": "CA",
        "declared_purpose": "Capability OS end-to-end test",
        "status": status,
        "trust_score": trust_score,
        "risk_tier": risk_tier,
        "evidence_head": "c0f5" * 16,
        "latest_genome_hash": "ab" * 32,
        "genome": {"safety_rules": [], "permissions": [], "risk_category": "low"},
        "parent_agent_ids": [],
    }


def _client(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> GnomledgerPGLClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real_client(transport=transport))
    return GnomledgerPGLClient(Settings(gnomledger_url="http://gnomledger.test", gnomledger_api_key="k"))


@pytest.mark.parametrize("status", ["registered", "active", "standard", "REGISTERED"])
def test_live_gnomledger_statuses_are_executable(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    client = _client(monkeypatch, _agent_payload(status))

    cert = client.validate_agent_for_execution("agent_b340a633c4b7")

    assert cert.is_active is True
    assert cert.status == status


def test_decommissioned_agent_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _agent_payload("decommissioned"))

    with pytest.raises(ValueError, match="is not active"):
        client.validate_agent_for_execution("agent_b340a633c4b7")


def test_unknown_status_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _agent_payload("suspended"))

    with pytest.raises(ValueError, match="is not active"):
        client.validate_agent_for_execution("agent_b340a633c4b7")


def test_low_trust_is_still_refused_for_a_live_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _agent_payload("registered", trust_score=40.0))

    with pytest.raises(ValueError, match="below termination threshold"):
        client.validate_agent_for_execution("agent_b340a633c4b7")
