"""Stale holder must not escape the epoch gate by omitting the epoch (H1).

Found by independent probe on live CAPPO: after POST /advance-epoch, a holder with the
original (still valid) token could execute by simply not sending `authority_epoch`,
because the gate in preflight only ran when the caller volunteered an epoch.

Scenarios (all through the public HTTP API, no monkeypatching):
  1. omitted epoch after advance            -> must be refused, target never invoked
  2. current epoch after advance            -> must still be allowed (positive control)
  3. omitted epoch before any advance       -> must still be allowed (compatibility)
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.effects import LocalRecordAdapter, TargetAdapterRegistry
from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.capability_mount.service import AnchorResult


class ConfirmedAnchor:
    def anchor(self, event_type: str, **_: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id=f"omitted-epoch-{event_type}-{uuid4().hex}")


def _setup(client: TestClient, adapter: LocalRecordAdapter) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(
        CapabilityPackage(
            id="records@v1",
            family="activation",
            title="Activation Records",
            purpose="Manage activation records",
            reads=["record.read"],
            writes=["record.create"],
            outputs=["record"],
            policy_defaults={"mode": "record"},
        )
    )
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(LocalRecordAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"


def _mount(client: TestClient) -> dict:
    res = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["decision"] == "allow"
    return body


def _execute(client: TestClient, mount: dict, resource: str, epoch: int | None) -> dict:
    tok = mount["token"]
    payload = {
        "token_id": tok["token_id"],
        "nonce": tok["nonce"],
        "action": "record.create",
        "target_ref": LocalRecordAdapter.ref,
        "resource": resource,
        "arguments": {"status": "active"},
        "operation_id": f"op-{uuid4().hex}",
    }
    if epoch is not None:
        payload["authority_epoch"] = epoch
    return client.post(f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=payload).json()


def test_omitted_epoch_after_advance_is_refused(client: TestClient, tmp_path: Path) -> None:
    adapter = LocalRecordAdapter(tmp_path)
    _setup(client, adapter)
    mount = _mount(client)
    adv = client.post(f"/v1/capability/mounts/{mount['mount']['id']}/advance-epoch", json={})
    assert adv.status_code == 200 and adv.json()["authority_epoch"] == 2

    body = _execute(client, mount, "omitted-after-advance", epoch=None)

    assert body["decision"] == "deny", f"stale holder escaped the fence by omitting epoch: {body}"
    assert adapter.invocation_count == 0
    assert not (tmp_path / "omitted-after-advance.json").exists()


def test_current_epoch_after_advance_is_allowed(client: TestClient, tmp_path: Path) -> None:
    adapter = LocalRecordAdapter(tmp_path)
    _setup(client, adapter)
    mount = _mount(client)
    client.post(f"/v1/capability/mounts/{mount['mount']['id']}/advance-epoch", json={})

    body = _execute(client, mount, "current-after-advance", epoch=2)

    assert body["decision"] == "allow"
    assert adapter.invocation_count == 1


def test_omitted_epoch_before_any_advance_is_allowed(client: TestClient, tmp_path: Path) -> None:
    adapter = LocalRecordAdapter(tmp_path)
    _setup(client, adapter)
    mount = _mount(client)

    body = _execute(client, mount, "omitted-no-advance", epoch=None)

    assert body["decision"] == "allow"
    assert adapter.invocation_count == 1
