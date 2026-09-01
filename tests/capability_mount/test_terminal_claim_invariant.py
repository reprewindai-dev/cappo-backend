from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

import cappo_backend.models.consequence_execution as consequence_execution
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from tests.capability_mount.test_execute_consequence import (
    FailingAdapter,
    UncertainAdapter,
    execute_payload,
    prepare,
)


def _events_for_operation(db, operation_id: str) -> list[ConsequenceExecutionEvent]:
    return db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == operation_id)
        .order_by(ConsequenceExecutionEvent.version)
    ).scalars().all()


def test_pre_invocation_fault_freezes_non_evidence_derived_allow(
    client,
    db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = "terminal-claim-pre-invocation"
    mount, adapter = prepare(client, tmp_path)

    original_build_intent_hash = consequence_execution.build_intent_hash
    calls = 0

    def build_intent_hash_with_fault(**kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected_pre_invocation_fault")
        return original_build_intent_hash(**kwargs)

    monkeypatch.setattr(
        "cappo_backend.models.consequence_execution.build_intent_hash",
        build_intent_hash_with_fault,
    )

    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(
            mount,
            operation_id=operation_id,
            resource="pre-invocation",
        ),
    ).json()

    # DEFECT (frozen deliberately): terminal claims must be evidence-derived.
    # No durable event exists for this operation, yet the response asserts
    # decision=allow. A correct implementation must report an uncertain or
    # non-terminal outcome here. Changing these assertions requires changing
    # the enforcement behavior first.
    assert body["decision"] == "allow"
    assert body["consequence"]["state"] is None
    assert body["consequence"]["receipt_id"] is None
    assert body["consequence"]["target_invoked"] is False
    assert adapter.invocations_by_action.get("record.create", 0) == 0
    assert not (tmp_path / "pre-invocation.json").exists()
    assert _events_for_operation(db, operation_id) == []


def test_post_invocation_fault_reports_failed_fsm_state(
    client,
    db,
    tmp_path: Path,
) -> None:
    operation_id = "terminal-claim-post-invocation"
    mount, adapter = prepare(client, tmp_path, adapter=FailingAdapter(tmp_path))

    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(
            mount,
            operation_id=operation_id,
            resource="post-invocation",
        ),
    ).json()

    events = _events_for_operation(db, operation_id)
    assert adapter.invocations_by_action["record.create"] == 1
    assert not (tmp_path / "post-invocation.json").exists()
    assert events[-1].state == "failed"
    # The request was admitted; the FSM carries the failed consequence outcome.
    assert body["decision"] == "allow"
    assert body["consequence"]["state"] == events[-1].state


def test_post_commit_fault_reports_outcome_unknown_without_reexecution(
    client,
    db,
    tmp_path: Path,
) -> None:
    operation_id = "terminal-claim-post-commit"
    mount, adapter = prepare(client, tmp_path, adapter=UncertainAdapter(tmp_path))

    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(
            mount,
            operation_id=operation_id,
            resource="post-commit",
        ),
    ).json()

    record_path = tmp_path / "post-commit.json"
    events = _events_for_operation(db, operation_id)
    assert json.loads(record_path.read_text(encoding="utf-8")) == {
        "status": "active",
        "attempt": 1,
    }
    assert body["consequence"]["target_invoked"] is True
    assert adapter.invocations_by_action["record.create"] == 1
    assert events[-1].state == "outcome_unknown"
    assert body["consequence"]["state"] == events[-1].state
