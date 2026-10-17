"""Physical verification: monotonic authority epoch commit-fencing.

Invariant H1 & H4:
A consequence authorized under authority_epoch = E1 must be deterministically
refused at begin_consequence if the mount's authority_epoch has advanced to E2
(lease rotation, authority transfer, successor activation).

Result:
- Consequence state is recorded FAILED with error_summary="stale_authority_epoch"
  and completion_proof_type="fence_refusal".
- Physical adapter is never dispatched (invocation_count == 0, delta_C == 0).
- The caller receives Decision.DENY with reason="stale_authority_epoch".
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import (
    LocalRecordAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    UnmountReason,
)
from cappo_backend.capability_mount.service import AnchorResult, MountRegistry
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent


class ConfirmedAnchor:
    def anchor(self, event_type: str, **_: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id=f"epoch-fence-{event_type}-{uuid4().hex}")


def records_package(package_id: str = "records@v1") -> CapabilityPackage:
    return CapabilityPackage(
        id=package_id,
        family="activation",
        title="Activation Records",
        purpose="Manage activation records",
        reads=["record.read"],
        writes=["record.create"],
        outputs=["record"],
        policy_defaults={"mode": "record"},
    )


def prepare_mount(client: TestClient, adapter: LocalRecordAdapter) -> dict[str, object]:
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(LocalRecordAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"
    mounted = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    )
    assert mounted.status_code == 200
    body = mounted.json()
    assert body["decision"] == "allow"
    return body


def execute_payload(mount: dict[str, object], operation_id: str) -> dict[str, object]:
    token = mount["token"]
    return {
        "token_id": token["token_id"],
        "nonce": token["nonce"],
        "action": "record.create",
        "target_ref": LocalRecordAdapter.ref,
        "resource": "epoch-fenced-1",
        "arguments": {"status": "active"},
        "operation_id": operation_id,
    }


def advance_mount_epoch(db: Session, mount_id: str, new_epoch: int = 2) -> int:
    """Advance the authority epoch under a separate session, simulating concurrent lease advancement."""
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as other:
        row = other.execute(
            select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        ).scalar_one()
        row.authority_epoch = new_epoch
        other.commit()
        return row.authority_epoch


def test_stale_authority_epoch_refuses_dispatch(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interleaving: Mount authority_epoch advances after evaluate() commits, before begin_consequence()."""
    adapter = LocalRecordAdapter(tmp_path)
    mount = prepare_mount(client, adapter)
    mount_id = mount["mount"]["id"]
    operation_id = f"epoch-op-{uuid4().hex}"
    advanced_epochs: list[int] = []

    original_evaluate = MountRegistry.evaluate

    def evaluate_then_advance_epoch(self, *args, **kwargs):
        result = original_evaluate(self, *args, **kwargs)
        if result[0] is Decision.ALLOW:
            # Mount remains live (terminated=False), but its authority epoch is advanced!
            new_ep = advance_mount_epoch(db, mount_id, new_epoch=2)
            advanced_epochs.append(new_ep)
        return result

    monkeypatch.setattr(MountRegistry, "evaluate", evaluate_then_advance_epoch)

    response = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=execute_payload(mount, operation_id),
    )
    body = response.json()

    # Verify epoch was bumped during evaluate
    assert len(advanced_epochs) == 1
    assert advanced_epochs[0] == 2

    # Physical consequence MUST be 0 (Delta C = 0)
    assert adapter.invocation_count == 0
    assert not (tmp_path / "epoch-fenced-1.json").exists()

    # Response must report DENY with stale_authority_epoch
    assert body["decision"] == "deny"
    assert body["reason"] == "stale_authority_epoch"

    # Verify event ledger in database
    db.commit()
    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == operation_id)
        .order_by(ConsequenceExecutionEvent.version.asc())
    ).scalars().all()

    assert len(events) == 2
    # Event 0: AUTHORIZED at epoch 1
    assert events[0].state == "authorized"
    assert events[0].authority_epoch == 1
    assert events[0].version == 0

    # Event 1: FAILED at epoch 2 (refused by fence)
    assert events[1].state == "failed"
    assert events[1].authority_epoch == 2
    assert events[1].error_summary == "stale_authority_epoch"
    assert events[1].completion_proof_type == "fence_refusal"
    assert events[1].version == 1
