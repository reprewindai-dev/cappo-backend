from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cappo_backend.capability_mount.effects import (
    ConsequenceContext,
    GovernedCounterAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.service import (
    GOVERNED_COUNTER_PACKAGE,
    AnchorResult,
)
from cappo_backend.config import Settings
from cappo_backend.main import create_app


class ConfirmedAnchor:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.events.append({"event_type": event_type, **payload})
        return AnchorResult("confirmed", anchor_id=f"anchor-{len(self.events)}")


def counter_context(
    action: str,
    *,
    resource: str = "demo-1",
    arguments: dict[str, object] | None = None,
) -> ConsequenceContext:
    return ConsequenceContext(
        action=action,
        resource=resource,
        arguments=arguments or {},
        operation_id=None,
    )


def configure_counter(client: TestClient, root: Path) -> GovernedCounterAdapter:
    registry = client.app.state.mount_registry
    registry.register_package(GOVERNED_COUNTER_PACKAGE)
    registry.anchor = ConfirmedAnchor()
    adapter = GovernedCounterAdapter(root)
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(GovernedCounterAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"
    return adapter


def mount_counter(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": GOVERNED_COUNTER_PACKAGE.id,
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {
                "reads": ["counter.read"],
                "writes": ["counter.increment"],
                "blocked": ["counter.reset"],
            },
            "ttl_seconds": 300,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    return body


def execute_payload(
    mount: dict[str, object],
    *,
    action: str = "counter.increment",
    resource: str = "demo-1",
    operation_id: str | None = None,
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    token = mount["token"]
    return {
        "token_id": token["token_id"],
        "nonce": token["nonce"],
        "action": action,
        "target_ref": GovernedCounterAdapter.ref,
        "resource": resource,
        "arguments": arguments or {},
        "operation_id": operation_id,
    }


def test_fresh_read_and_exactly_one_increment_per_invocation(tmp_path: Path) -> None:
    adapter = GovernedCounterAdapter(tmp_path)

    assert adapter.dispatch(counter_context("counter.read")) == {
        "resource": "demo-1",
        "value": 0,
        "version": 0,
    }
    first = adapter.dispatch(counter_context("counter.increment"))
    second = adapter.dispatch(
        counter_context("counter.increment", arguments={"by": 5})
    )

    assert first == {
        "resource": "demo-1",
        "previous_value": 0,
        "value": 1,
        "version": 1,
    }
    assert second == {
        "resource": "demo-1",
        "previous_value": 1,
        "value": 2,
        "version": 2,
    }
    assert json.loads((tmp_path / "counter_demo-1.json").read_text()) == {
        "value": 2,
        "version": 2,
    }
    assert adapter.invocation_count == 3
    assert adapter.invocations_by_action == {
        "counter.read": 1,
        "counter.increment": 2,
    }


def test_invalid_counter_resource_is_rejected(tmp_path: Path) -> None:
    adapter = GovernedCounterAdapter(tmp_path)

    with pytest.raises(ValueError, match="invalid_target_resource"):
        adapter.dispatch(counter_context("counter.read", resource="../escape"))


def test_effect_root_enables_builtin_counter_registration(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            capability_effect_record_root=str(tmp_path),
            auth_enabled=False,
        )
    )

    assert app.state.mount_registry.packages[GOVERNED_COUNTER_PACKAGE.id] == (
        GOVERNED_COUNTER_PACKAGE
    )
    assert isinstance(
        app.state.mount_registry.target_adapters.resolve(GovernedCounterAdapter.ref),
        GovernedCounterAdapter,
    )


def test_governed_counter_route_flow(
    client: TestClient,
    tmp_path: Path,
) -> None:
    adapter = configure_counter(client, tmp_path)

    package_ids = {package["id"] for package in client.get("/v1/capability/packages").json()}
    assert GOVERNED_COUNTER_PACKAGE.id in package_ids

    mount = mount_counter(client)
    mount_id = mount["mount"]["id"]
    token = mount["token"]

    blocked = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "counter.reset",
            "resource": "demo-1",
        },
    )
    blocked_body = blocked.json()
    assert blocked_body["decision"] == "deny"
    assert blocked_body["reason"] == "lease_invariant_violation"
    status_after_block = client.get(f"/v1/capability/mounts/{mount_id}").json()
    assert status_after_block["nonce_consumed"] is False

    first = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=execute_payload(mount, operation_id="op-1", arguments={"by": 5}),
    )
    first_body = first.json()
    assert first_body["decision"] == "allow"
    assert first_body["consequence"]["resulting_state"]["value"] == 1
    assert first_body["consequence"]["receipt_id"]
    assert first_body["consequence"]["terminated"] is True
    assert adapter.invocation_count == 1

    replay = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=execute_payload(mount, operation_id="op-1", arguments={"by": 5}),
    )
    replay_body = replay.json()
    assert replay_body["decision"] == "deny"
    assert replay_body["reason"].startswith("idempotency_replay:")
    assert adapter.invocation_count == 1
    assert json.loads((tmp_path / "counter_demo-1.json").read_text())["value"] == 1

    terminated_mount = mount_counter(client)
    terminated_id = terminated_mount["mount"]["id"]
    terminate = client.post(
        f"/v1/capability/mounts/{terminated_id}/terminate",
        json={"reason": "explicit_terminate"},
    )
    assert terminate.json()["decision"] == "allow"
    terminated_execute = client.post(
        f"/v1/capability/mounts/{terminated_id}/execute",
        json=execute_payload(terminated_mount, operation_id="op-terminated"),
    )
    terminated_body = terminated_execute.json()
    assert terminated_body["decision"] == "deny"
    assert terminated_body["reason"] == "execution is terminated"
    assert adapter.invocation_count == 1
