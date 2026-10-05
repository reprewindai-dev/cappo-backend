"""Target-side authority check ("Model B"): a target redeems a permit before committing.

Before this, authority was only checked inside CAPPO: a target accepted anything that
carried the gateway's static credential, so a permit captured before revocation, or the
credential alone, was enough to write. Now the target asks CAPPO at the moment of commit.

Contract under test (decided under the mount row lock that terminate() takes):
- ALLOW only for CAPPO's own STARTED consequence on a live mount, with a permit matching
  the exact payload, once;
- DENY after terminate, for a different payload, on replay, for unknown operations, and
  when permits are not configured.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import (
    ConsequenceContext,
    LocalRecordAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.errors import TargetRefusedError
from cappo_backend.capability_mount.models import Decision
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.models.consequence_redemption import ConsequenceRedemption
from tests.capability_mount.test_terminate_fence import (
    ConfirmedAnchor,
    execute_payload,
    op_states,
    owner_terminate,
    records_package,
)

PERMIT_KEY = "unit-test-permit-key"


class RedeemingTarget(LocalRecordAdapter):
    """A target that asks CAPPO before it writes, as an external sink would over HTTP."""

    def __init__(self, root: Path, db: Session, settings: object, *, before_redeem=None, tamper=False, twice=False):
        super().__init__(root)
        self.db = db
        self.settings = settings
        self.before_redeem = before_redeem
        self.tamper = tamper
        self.twice = twice
        self.decisions: list[tuple[Decision, str]] = []
        self.seen: dict[str, str] = {}

    def _redeem(self, operation_id: str, permit: str, digest: str) -> tuple[Decision, str]:
        with Session(bind=self.db.get_bind(), autoflush=False, expire_on_commit=False) as other:
            registry = MountRegistry(db=other, anchor=ConfirmedAnchor(), settings=self.settings)
            return registry.redeem_consequence(operation_id, permit, digest, sink_ref="unit-target")

    def dispatch(self, context: ConsequenceContext) -> object:
        assert context.permit_for is not None, "CAPPO must hand the target a permit"
        payload = json.dumps(dict(context.arguments), sort_keys=True).encode()
        permit = context.permit_for(payload)
        digest = hashlib.sha256(payload).hexdigest()
        self.seen = {"operation_id": context.operation_id, "permit": permit, "digest": digest}
        if self.before_redeem:
            self.before_redeem()
        if self.tamper:
            digest = hashlib.sha256(payload + b"tampered").hexdigest()
        result = self._redeem(context.operation_id, permit, digest)
        self.decisions.append(result)
        if self.twice:
            self.decisions.append(self._redeem(context.operation_id, permit, digest))
        if result[0] is not Decision.ALLOW:
            raise TargetRefusedError(f"target_refused:{result[1]}")
        return super().dispatch(context)


def prepare(client: TestClient, adapter: LocalRecordAdapter, *, key: str = PERMIT_KEY) -> dict:
    client.app.state.settings.consequence_permit_key = key
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = ConfirmedAnchor()
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


def run(client: TestClient, mount: dict, operation_id: str) -> dict:
    payload = execute_payload(mount, operation_id)
    payload["arguments"] = {"status": "active", "mount_id": mount["mount"]["id"]}
    return client.post(f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=payload).json()


def test_live_started_consequence_redeems_once_and_commits(client: TestClient, db: Session, tmp_path: Path) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings, twice=True)
    mount = prepare(client, target)
    operation_id = f"op-{uuid4().hex}"

    body = run(client, mount, operation_id)

    assert target.decisions[0] == (Decision.ALLOW, "redeemed")
    assert target.decisions[1] == (Decision.DENY, "already_redeemed")  # replay while still in flight
    assert body["consequence"]["state"] == "succeeded"
    assert (tmp_path / "fenced-1.json").exists()
    db.commit()
    row = db.execute(select(ConsequenceRedemption).where(ConsequenceRedemption.operation_id == operation_id)).scalar_one()
    assert row.payload_sha256 == target.seen["digest"] and row.sink_ref == "unit-target"
    # After the consequence settled the mount is terminated: the same permit is dead.
    assert target._redeem(operation_id, target.seen["permit"], target.seen["digest"]) == (Decision.DENY, "mount_terminated")


def test_revocation_between_dispatch_and_commit_is_refused_at_the_target(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    """Post-admission, pre-reality revocation: CAPPO admitted and dispatched, then authority ended."""
    fence: list[tuple] = []
    target = RedeemingTarget(tmp_path, db, client.app.state.settings)
    mount = prepare(client, target)
    mount_id = mount["mount"]["id"]
    target.before_redeem = lambda: fence.append(owner_terminate(db, mount_id, settle_seconds=0.05))
    operation_id = f"op-{uuid4().hex}"

    body = run(client, mount, operation_id)

    decision, reason, _anchor, in_flight = fence[0]
    assert (decision, reason, in_flight) == (Decision.ALLOW, "terminated_in_flight", [operation_id])
    assert target.decisions == [(Decision.DENY, "mount_terminated")]
    assert not (tmp_path / "fenced-1.json").exists(), "the admitted consequence must not become reality"
    assert target.invocation_count == 0
    assert body["consequence"]["state"] == "failed"
    assert op_states(db, operation_id) == ["authorized", "started", "failed"]
    db.commit()
    assert db.execute(select(ConsequenceRedemption)).scalars().all() == []


def test_permit_is_bound_to_the_exact_payload(client: TestClient, db: Session, tmp_path: Path) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings, tamper=True)
    mount = prepare(client, target)

    body = run(client, mount, f"op-{uuid4().hex}")

    assert target.decisions == [(Decision.DENY, "permit_mismatch")]
    assert not (tmp_path / "fenced-1.json").exists()
    assert body["consequence"]["state"] == "failed"


def test_redeem_route_refuses_what_cappo_did_not_dispatch(client: TestClient, tmp_path: Path) -> None:
    prepare(client, LocalRecordAdapter(tmp_path))
    response = client.post(
        "/v1/capability/redeem",
        json={"operation_id": "never-dispatched", "permit": "f" * 64, "payload_sha256": "0" * 64},
    )
    assert response.status_code == 200
    assert response.json() == {"decision": "deny", "reason": "unknown_operation", "operation_id": "never-dispatched"}


def test_without_a_permit_key_nothing_can_be_redeemed(client: TestClient, db: Session, tmp_path: Path) -> None:
    seen: list[object] = []

    class Plain(LocalRecordAdapter):
        def dispatch(self, context: ConsequenceContext) -> object:
            seen.append(context.permit_for)
            return super().dispatch(context)

    mount = prepare(client, Plain(tmp_path), key="")
    client.app.state.settings.approval_token_signing_key = ""
    run(client, mount, f"op-{uuid4().hex}")
    assert seen == [None]
    registry = MountRegistry(db=db, anchor=ConfirmedAnchor(), settings=client.app.state.settings)
    assert registry.redeem_consequence("anything", "x", "0" * 64) == (Decision.DENY, "permits_not_configured")
