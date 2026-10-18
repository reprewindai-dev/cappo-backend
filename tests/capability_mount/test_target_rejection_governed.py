"""A target that rejects its own input after STARTED is a governed deny, not an HTTP 500.

Other exceptions keep their existing contract (see test_terminal_claim_invariant:
RuntimeError still propagates and the FSM records FAILED).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from cappo_backend.capability_mount.effects import ConsequenceContext, LocalRecordAdapter
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from tests.capability_mount.test_execute_consequence import execute_payload, prepare


class RejectingAdapter(LocalRecordAdapter):
    def dispatch(self, context: ConsequenceContext) -> object:
        self.invocation_count += 1
        raise ValueError("invalid_target_resource")


def test_target_input_rejection_is_a_governed_deny_with_failed_state(client, db, tmp_path: Path) -> None:
    operation_id = "target-rejection-governed-deny"
    mount, adapter = prepare(client, tmp_path, adapter=RejectingAdapter(tmp_path))

    response = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, operation_id=operation_id, resource="rejected-input"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "deny"
    assert body["reason"] == "target_rejected:invalid_target_resource"
    assert not (tmp_path / "rejected-input.json").exists()

    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == operation_id)
        .order_by(ConsequenceExecutionEvent.version)
    ).scalars().all()
    assert [e.state for e in events] == ["authorized", "started", "failed"]
