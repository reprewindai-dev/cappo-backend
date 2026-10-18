"""Contract test: GnomledgerPGLAdapter must emit full pre/post execution details.

gnomledger validates ``details`` for ``pre_execution_authorization`` and
``post_execution_attestation`` events against ``PreExecutionAuthorizationDetails`` /
``PostExecutionAttestationDetails``. A sparse payload is rejected with 422 and the
attestation is lost. gnomledger is a separate service and is not importable here, so
this test asserts the dict shape against the required field set it enforces (the
gnomledger-side test ``test_cappo_attestation_payloads.py`` posts the same shapes and
checks they are accepted).
"""

from __future__ import annotations

from typing import Any

from cappo_backend.config import Settings
from cappo_backend.services.gnomledger_pgl_client import GnomledgerAgentCertificate
from cappo_backend.services.pgl_adapter import GnomledgerPGLAdapter
from cappo_backend.services.pgl_client import PostCertificateParams, PreCertificateParams

# Required (non-optional) fields of the gnomledger schemas as of this contract.
PRE_REQUIRED = {
    "schema_version",
    "run_id",
    "workspace_id",
    "agent_id",
    "genome_hash",
    "constitution_hash",
    "plan_hash",
    "governance_decision",
    "risk_tier",
    "approved_budget_cents",
    "reserve_cents",
    "provenance",
}
POST_REQUIRED = {
    "schema_version",
    "run_id",
    "agent_id",
    "pre_authorization_event_id",
    "output_hash",
    "outcome_hash",
    "governance_decision",
    "provenance",
}


class _FakeGnomledger:
    """Captures the attestation calls the adapter makes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def validate_agent_for_execution(self, agent_id: str) -> GnomledgerAgentCertificate:
        return GnomledgerAgentCertificate(
            agent_id=agent_id,
            certificate_id="cert-1",
            name="agent",
            creator="owner",
            jurisdiction="ws-1",
            declared_purpose="demo",
            status="active",
            trust_score=90.0,
            risk_tier="standard",
            evidence_head=None,
            genome_hash="gh",
            safety_rules=[],
            permissions=[],
            risk_category="medium",
            is_active=True,
        )

    def record_execution_attestation(
        self, agent_id: str, event_type: str, summary: str, details: dict[str, Any]
    ) -> str:
        self.calls.append(
            {"agent_id": agent_id, "event_type": event_type, "summary": summary, "details": details}
        )
        return "evt_pre_abc123"


def _adapter() -> tuple[GnomledgerPGLAdapter, _FakeGnomledger]:
    adapter = GnomledgerPGLAdapter(settings=Settings(gnomledger_url="http://gnomledger.test"))
    fake = _FakeGnomledger()
    adapter._gnomledger = fake  # noqa: SLF001 - test seam
    return adapter, fake


def test_pre_execution_details_match_gnomledger_schema() -> None:
    adapter, fake = _adapter()
    params = PreCertificateParams(
        run_id="run-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        actor_id="pgl-actor",
        genome_hash="gh",
        constitution_hash="ch",
        plan_hash="ph",
        governance_decision="ALLOW",
        risk_tier="standard",
        approved_budget_cents=1000,
        reserve_cents=200,
        input_hash="ih",
        decision_frame_hash="dfh",
    )

    adapter.mint_pre_certificate(params)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["event_type"] == "pre_execution_authorization"
    details = call["details"]
    assert PRE_REQUIRED.issubset(details.keys()), PRE_REQUIRED - details.keys()
    assert details["schema_version"] == "pgl.pre_execution_authorization.v1"
    assert details["run_id"] == "run-1"
    assert details["workspace_id"] == "ws-1"
    assert details["agent_id"] == "agent-1"
    assert details["constitution_hash"] == "ch"
    assert details["plan_hash"] == "ph"
    assert details["governance_decision"] == "ALLOW"
    assert isinstance(details["provenance"], dict)
    assert details["provenance"]["recorded_by"] == "cappo-backend"


def test_post_execution_details_match_gnomledger_schema_and_link_pre_event() -> None:
    adapter, fake = _adapter()
    params = PostCertificateParams(
        pre_certificate_id="evt_pre_abc123",
        run_id="run-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        actor_id="pgl-actor",
        genome_hash="gh",
        constitution_hash="ch",
        plan_hash="ph",
        governance_decision="ALLOW",
        risk_tier="standard",
        output_hash="oh",
        outcome_hash="ch2",
        model_used="test-provider/test-model",
    )

    adapter.mint_post_certificate(params)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["event_type"] == "post_execution_attestation"
    details = call["details"]
    assert POST_REQUIRED.issubset(details.keys()), POST_REQUIRED - details.keys()
    assert details["schema_version"] == "pgl.post_execution_attestation.v1"
    assert details["run_id"] == "run-1"
    assert details["agent_id"] == "agent-1"
    # pre_authorization_event_id must be the event_id gnomledger returned for the pre event.
    assert details["pre_authorization_event_id"] == "evt_pre_abc123"
    assert details["output_hash"] == "oh"
    assert details["outcome_hash"] == "ch2"
    assert details["governance_decision"] == "ALLOW"
    assert details["model_used"] == "test-provider/test-model"
    assert isinstance(details["provenance"], dict)


def test_post_execution_omits_model_used_when_absent() -> None:
    adapter, fake = _adapter()
    params = PostCertificateParams(
        pre_certificate_id="evt_pre_abc123",
        run_id="run-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        genome_hash="gh",
        constitution_hash="ch",
        plan_hash="ph",
        governance_decision="ALLOW",
        risk_tier="standard",
        output_hash="oh",
        outcome_hash="ch2",
    )

    adapter.mint_post_certificate(params)

    details = fake.calls[0]["details"]
    # Absent model is simply not sent (gnomledger treats it as optional/null).
    assert "model_used" not in details
