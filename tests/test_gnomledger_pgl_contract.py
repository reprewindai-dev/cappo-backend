from types import SimpleNamespace

from cappo_backend.services.pgl_adapter import GnomledgerPGLAdapter
from cappo_backend.services.pgl_client import PostCertificateParams, PreCertificateParams


class RecordingGnomledger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def validate_agent_for_execution(self, agent_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            agent_id=agent_id,
            certificate_id="agent-certificate",
            name="Contract agent",
            creator="test",
            jurisdiction="workspace-1",
            declared_purpose="contract verification",
            status="active",
            trust_score=95.0,
            risk_tier="production",
            evidence_head=None,
            genome_hash="genome-hash",
            safety_rules=[],
            permissions=[],
            risk_category="low",
            is_active=True,
            parent_agent_ids=[],
        )

    def record_execution_attestation(self, **event: object) -> str:
        self.events.append(event)
        return f"event-{len(self.events)}"


def _adapter() -> tuple[GnomledgerPGLAdapter, RecordingGnomledger]:
    adapter = object.__new__(GnomledgerPGLAdapter)
    recorder = RecordingGnomledger()
    adapter._gnomledger = recorder
    adapter._local = None
    adapter._settings = SimpleNamespace(cappo_allow_noncanonical_pgl_fallback=False)
    adapter._cert_cache = {}
    return adapter, recorder


def test_pre_authorization_matches_gnomledger_execution_schema() -> None:
    adapter, recorder = _adapter()

    certificate = adapter.mint_pre_certificate(
        PreCertificateParams(
            run_id="run-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            genome_hash="genome-hash",
            constitution_hash="constitution-hash",
            plan_hash="plan-hash",
            input_hash="input-hash",
            decision_frame_hash="decision-frame-hash",
            governance_decision="ALLOW",
            risk_tier="production",
            approved_budget_cents=500,
            reserve_cents=100,
            actor_id="principal-1",
            provenance={"lease_id": "lease-1"},
        )
    )

    event = recorder.events[0]
    assert certificate.certificate_id == "event-1"
    assert event["event_type"] == "pre_execution_authorization"
    assert event["details"] == {
        "schema_version": "pgl.pre_execution_authorization.v1",
        "run_id": "run-1",
        "workspace_id": "workspace-1",
        "agent_id": "agent-1",
        "genome_hash": "genome-hash",
        "constitution_hash": "constitution-hash",
        "plan_hash": "plan-hash",
        "input_hash": "input-hash",
        "decision_frame_hash": "decision-frame-hash",
        "governance_decision": "ALLOW",
        "risk_tier": "production",
        "approved_budget_cents": 500,
        "reserve_cents": 100,
        "actor_id": "principal-1",
        "provenance": {"lease_id": "lease-1"},
        "standards_compliance": [],
    }


def test_post_attestation_links_the_pre_authorization_event() -> None:
    adapter, recorder = _adapter()

    adapter.mint_post_certificate(
        PostCertificateParams(
            pre_certificate_id="pre-event-1",
            run_id="run-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            genome_hash="genome-hash",
            constitution_hash="constitution-hash",
            plan_hash="plan-hash",
            governance_decision="ALLOW",
            risk_tier="production",
            output_hash="output-hash",
            outcome_hash="outcome-hash",
            actor_id="principal-1",
            provenance={"execution_id": "execution-1"},
        )
    )

    event = recorder.events[0]
    assert event["event_type"] == "post_execution_attestation"
    assert event["details"] == {
        "schema_version": "pgl.post_execution_attestation.v1",
        "run_id": "run-1",
        "agent_id": "agent-1",
        "pre_authorization_event_id": "pre-event-1",
        "output_hash": "output-hash",
        "outcome_hash": "outcome-hash",
        "governance_decision": "ALLOW",
        "actor_id": "principal-1",
        "provenance": {"execution_id": "execution-1"},
        "standards_compliance": [],
    }


def test_evidence_seal_uses_supported_custom_event_with_semantic_subtype() -> None:
    adapter, recorder = _adapter()

    result = adapter.append_evidence_event(
        certificate_id="post-event-1",
        agent_id="agent-1",
        event_type="capi_evidence_sealed",
        evidence={"eee_hash": "eee-hash"},
    )

    event = recorder.events[0]
    assert result == {"event_id": "event-1"}
    assert event["event_type"] == "custom"
    assert event["details"] == {
        "semantic_event_type": "capi_evidence_sealed",
        "certificate_id": "post-event-1",
        "evidence_seal": {"eee_hash": "eee-hash"},
    }
