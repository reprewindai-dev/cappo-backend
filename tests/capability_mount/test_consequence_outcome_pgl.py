"""Regression: PGL records consequence outcomes, not only authority events.

Lineage re-formation experiment (production profile, 692 queued-after-terminate
trials): the sink stayed unchanged, but PGL held only mount -> action_decision ->
terminate for each refused operation. A reader of the ledger alone could not tell
"authorized and never executed" from "authorized and executed".

Contract under test:
- a fence refusal appends ``consequence_refused`` (decision deny, mount_terminated);
- a completed consequence appends ``consequence_succeeded``;
- both carry the operation_id and receipt_id that thread them to the authority
  events for the same mount, and the anchor payload keeps those fields.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import LocalRecordAdapter, TargetAdapterRegistry
from cappo_backend.capability_mount.models import Decision
from cappo_backend.capability_mount.service import AnchorResult, MountRegistry
from cappo_backend.config import Settings
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.services.mount_pgl import AuditPGLAnchor

from tests.capability_mount.test_terminate_fence import execute_payload, owner_terminate, records_package


class RecordingAnchor:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def anchor(self, event_type: str, **kwargs: object) -> AnchorResult:
        self.events.append((event_type, kwargs))
        return AnchorResult("confirmed", anchor_id=f"rec-{event_type}-{uuid4().hex}")

    def outcomes(self, operation_id: str) -> list[tuple[str, dict[str, object]]]:
        return [
            (event_type, kw)
            for event_type, kw in self.events
            if event_type.startswith("consequence_") and kw.get("operation_id") == operation_id
        ]


def prepare(client: TestClient, adapter: LocalRecordAdapter, anchor: RecordingAnchor) -> dict:
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = anchor
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(LocalRecordAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"
    body = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    ).json()
    assert body["decision"] == "allow"
    return body


def test_succeeded_consequence_is_anchored_with_operation_identity(
    client: TestClient, tmp_path: Path
) -> None:
    anchor = RecordingAnchor()
    mount = prepare(client, LocalRecordAdapter(tmp_path), anchor)
    operation_id = f"op-{uuid4().hex}"

    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, operation_id),
    ).json()

    assert body["consequence"]["state"] == "succeeded"
    outcomes = anchor.outcomes(operation_id)
    assert [event_type for event_type, _ in outcomes] == ["consequence_succeeded"]
    _, kw = outcomes[0]
    assert kw["consequence_state"] == "succeeded"
    assert kw["decision"] == Decision.ALLOW.value
    assert kw["receipt_id"] == body["consequence"]["receipt_id"]
    assert kw["mount"].id == mount["mount"]["id"]


def test_fence_refusal_is_anchored_and_nothing_succeeds(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = RecordingAnchor()
    adapter = LocalRecordAdapter(tmp_path)
    mount = prepare(client, adapter, anchor)
    mount_id = mount["mount"]["id"]
    operation_id = f"op-{uuid4().hex}"

    original_evaluate = MountRegistry.evaluate

    def evaluate_then_fence(self, *args, **kwargs):
        result = original_evaluate(self, *args, **kwargs)
        if result[0] is Decision.ALLOW:
            owner_terminate(db, mount_id)
        return result

    monkeypatch.setattr(MountRegistry, "evaluate", evaluate_then_fence)
    body = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=execute_payload(mount, operation_id),
    ).json()

    assert body["reason"] == "mount_terminated"
    assert adapter.invocation_count == 0
    outcomes = anchor.outcomes(operation_id)
    assert [event_type for event_type, _ in outcomes] == ["consequence_refused"]
    _, kw = outcomes[0]
    assert kw["decision"] == Decision.DENY.value
    assert kw["reason"] == "mount_terminated"
    assert kw["proof_type"] == "fence_refusal"
    assert kw["receipt_id"]  # the authorization receipt it refused to act on


def test_pgl_anchor_payload_keeps_consequence_identity(db: Session) -> None:
    anchor = AuditPGLAnchor(db, settings=Settings(pgl_ledger_url=None))
    result = anchor.anchor(
        "consequence_refused",
        action="record.create",
        decision="deny",
        reason="mount_terminated",
        mount=None,
        token=None,
        operation_id="op-123",
        receipt_id="rcpt-9",
        consequence_state="refused",
        proof_type="fence_refusal",
    )
    db.commit()
    assert result.status == "pending_reconciliation"
    event = db.execute(
        select(AuditEvent).where(AuditEvent.operation_type == "capability_mount_consequence_refused")
    ).scalars().one()
    assert event.payload["operation_id"] == "op-123"
    assert event.payload["receipt_id"] == "rcpt-9"
    assert event.payload["consequence_state"] == "refused"
    assert event.payload["proof_type"] == "fence_refusal"
