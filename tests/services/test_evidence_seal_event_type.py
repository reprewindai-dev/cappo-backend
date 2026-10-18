"""Evidence seals must use a ledger event_type gnomledger accepts.

record_evidence_seal appends with event_type="capi_evidence_sealed", which is not one
of gnomledger's accepted LedgerEventCreate literals — sent verbatim it is rejected with
422 and the seal is lost. The adapter must record it as a "custom" ledger event while
preserving the semantic CAPPO event type in the details.
"""

from __future__ import annotations

from typing import Any

from cappo_backend.config import Settings
from cappo_backend.services.pgl_adapter import GnomledgerPGLAdapter

# gnomledger's accepted LedgerEventCreate.event_type literals.
ACCEPTED_LEDGER_EVENT_TYPES = {
    "birth_registration",
    "mutation_update",
    "test_audit",
    "deployment",
    "incident",
    "violation",
    "pre_execution_authorization",
    "post_execution_attestation",
    "custom",
    "decommission",
}


class _FakeGnomledger:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record_execution_attestation(self, agent_id, event_type, summary, details) -> str:
        self.calls.append(
            {"agent_id": agent_id, "event_type": event_type, "summary": summary, "details": details}
        )
        return "evt_seal_1"


def test_evidence_seal_uses_accepted_event_type_and_preserves_semantics() -> None:
    adapter = GnomledgerPGLAdapter(settings=Settings(gnomledger_url="http://gnomledger.test"))
    fake = _FakeGnomledger()
    adapter._gnomledger = fake  # noqa: SLF001 - test seam

    result = adapter.append_evidence_event(
        certificate_id="cert-1",
        event_type="capi_evidence_sealed",
        evidence={"seal_hash": "sha256:seal"},
        agent_id="agent-1",
    )

    assert result == {"event_id": "evt_seal_1"}
    call = fake.calls[0]
    # The wire event_type must be one gnomledger accepts (never the raw seal type).
    assert call["event_type"] in ACCEPTED_LEDGER_EVENT_TYPES
    assert call["event_type"] == "custom"
    # The real CAPPO event type is preserved for auditing.
    assert call["details"]["cappo_event_type"] == "capi_evidence_sealed"
    assert call["details"]["evidence_seal"] == {"seal_hash": "sha256:seal"}
