"""Seam 1: /v1/exec surfaces the real PGL ledger ids so a client can fetch and
verify the persisted evidence event (there is no per-execution CAPPO evidence route).
"""

from __future__ import annotations

from types import SimpleNamespace

from cappo_backend.api.routers.exec_router import _pgl_block
from cappo_backend.config import Settings
from cappo_backend.services.gnomledger_pgl_client import GnomledgerAgentCertificate
from cappo_backend.services.pgl_adapter import GnomledgerPGLAdapter
from cappo_backend.services.pgl_client import PostCertificateParams


def test_pgl_block_surfaces_recorded_ledger_ids() -> None:
    run = SimpleNamespace(
        pgl_identity={
            "pre_execution_certificate_id": "evt_pre",
            "post_execution_certificate_id": "evt_post",
            "capi_evidence_event_id": "evt_seal",
            "persisted": True,
        }
    )
    block = _pgl_block(run)
    assert block == {
        "pre_execution_certificate_id": "evt_pre",
        "post_execution_certificate_id": "evt_post",
        "capi_evidence_event_id": "evt_seal",
        "persisted": True,
    }


def test_pgl_block_is_none_when_nothing_recorded() -> None:
    assert _pgl_block(SimpleNamespace(pgl_identity={})) is None
    assert _pgl_block(None) is None


class _FakeGnomledger:
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
        )

    def record_execution_attestation(self, agent_id, event_type, summary, details) -> str:
        return "evt_post_real_id"


def test_post_certificate_id_is_the_real_gnomledger_post_event_id() -> None:
    adapter = GnomledgerPGLAdapter(settings=Settings(gnomledger_url="http://gnomledger.test"))
    adapter._gnomledger = _FakeGnomledger()  # noqa: SLF001 - test seam

    cert = adapter.mint_post_certificate(
        PostCertificateParams(
            pre_certificate_id="evt_pre_id",
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
    )

    # The post attestation's own ledger event id is now threaded back so the client
    # can retrieve the outcome evidence, not just the pre-authorization.
    assert cert.certificate_id == "evt_post_real_id"
