from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.effects import (
    CappoUncertainError,
    EffectTargetRegistry,
    LocalRecordAdapter,
)
from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.capability_mount.service import AnchorResult


class ConfirmedAnchor:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.events.append({"event_type": event_type, **payload})
        return AnchorResult("confirmed", anchor_id=f"anchor-{len(self.events)}")


class FailingAdapter(LocalRecordAdapter):
    def invoke(self, action: str, resource: str, arguments: dict[str, object]) -> object:
        self.invocation_count += 1
        self.invocations_by_action[action] = self.invocations_by_action.get(action, 0) + 1
        raise RuntimeError("effect_failed_before_write")


class UncertainAdapter(LocalRecordAdapter):
    def invoke(self, action: str, resource: str, arguments: dict[str, object]) -> object:
        result = super().invoke(action, resource, arguments)
        raise CappoUncertainError("effect_outcome_unknown")


class CreateOnlyAdapter(LocalRecordAdapter):
    actions = frozenset({"record.create"})


def records_package() -> CapabilityPackage:
    return CapabilityPackage(
        id="records@v1",
        family="activation",
        title="Activation Records",
        purpose="Manage activation records",
        reads=["record.read"],
        writes=["record.create", "record.delete"],
        blocked=["record.delete"],
        outputs=["record"],
        policy_defaults={"mode": "record"},
    )


def prepare(
    client: TestClient,
    tmp_path: Path,
    adapter: LocalRecordAdapter | None = None,
    ttl_seconds: int = 300,
) -> tuple[dict[str, object], LocalRecordAdapter]:
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = ConfirmedAnchor()
    selected = adapter or LocalRecordAdapter(tmp_path)
    registry.effect_targets = EffectTargetRegistry()
    registry.effect_targets.register(LocalRecordAdapter.ref, selected)
    client.headers["X-Workspace-ID"] = "w1"
    mounted = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {
                "reads": [],
                "writes": ["record.create", "record.delete"],
                "blocked": ["record.delete"],
            },
            "ttl_seconds": ttl_seconds,
        },
    )
    assert mounted.status_code == 200
    body = mounted.json()
    assert body["decision"] == "allow"
    return body, selected


def execute_payload(mount: dict[str, object], **overrides: object) -> dict[str, object]:
    token = mount["token"]
    payload: dict[str, object] = {
        "token_id": token["token_id"],
        "nonce": token["nonce"],
        "action": "record.create",
        "target_ref": LocalRecordAdapter.ref,
        "resource": "activation-1",
        "arguments": {"status": "active", "attempt": 1},
    }
    payload.update(overrides)
    return payload


def test_allowed_create_writes_record_and_terminates(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    response = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    assert body["consequence"]["state"] == "succeeded"
    assert body["consequence"]["target_invoked"] is True
    assert body["consequence"]["resulting_state"] == {"status": "active", "attempt": 1}
    assert body["authority"]["nonce_consumed"] is True
    assert body["consequence"]["resource"] == "activation-1"
    assert adapter.invocation_count == 1
    assert json.loads((tmp_path / "activation-1.json").read_text()) == {
        "status": "active",
        "attempt": 1,
    }
    assert body["consequence"]["resulting_state"] is not None


def test_blocked_delete_does_not_invoke_adapter(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    record_path = tmp_path / "activation-1.json"
    record_path.write_text('{"status":"active"}')

    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, action="record.delete"),
    ).json()

    assert body["decision"] == "deny"
    assert body["reason"] == "blocked_action"
    assert body["consequence"]["target_invoked"] is False
    assert adapter.invocation_count == 0
    assert record_path.read_text() == '{"status":"active"}'


def test_action_outside_profile_is_denied(client: TestClient, tmp_path: Path) -> None:
    mount, adapter = prepare(client, tmp_path)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, action="record.read"),
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "not_in_capability_profile"
    assert adapter.invocation_count == 0


def test_wrong_workspace_is_denied(client: TestClient, tmp_path: Path) -> None:
    settings = client.app.state.settings
    settings.auth_enabled = True
    settings.api_keys = "owner-key"
    client.headers["X-API-Key"] = "owner-key"
    mount, adapter = prepare(client, tmp_path)
    client.headers["X-Workspace-ID"] = "w2"
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "owner_mismatch"
    assert adapter.invocation_count == 0


def test_unknown_target_is_denied_without_writing(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, target_ref="missing.target"),
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "unknown_effect_target"
    assert adapter.invocation_count == 0
    assert list(tmp_path.iterdir()) == []


def test_target_url_is_rejected_by_request_schema(
    client: TestClient, tmp_path: Path
) -> None:
    mount, _adapter = prepare(client, tmp_path)
    response = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, target_url="http://untrusted.example"),
    )
    assert response.status_code == 422


def test_replay_does_not_invoke_adapter_again(client: TestClient, tmp_path: Path) -> None:
    mount, adapter = prepare(client, tmp_path)
    request = execute_payload(mount, operation_id="operation-replay")
    first = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=request
    ).json()
    second = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=request
    ).json()
    assert first["decision"] == "allow"
    assert second["decision"] == "deny"
    assert adapter.invocation_count == 1


def test_operation_id_same_intent_returns_canonical_replay(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    request = execute_payload(mount, operation_id="operation-same-intent")
    first = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=request
    ).json()
    second = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=request
    ).json()
    assert first["consequence"]["state"] == "succeeded"
    assert second["decision"] == "deny"
    assert second["reason"] == "idempotency_replay:succeeded"
    assert second["consequence"]["state"] == "succeeded"
    assert adapter.invocation_count == 1


def test_terminated_mount_does_not_invoke_adapter(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/terminate",
        json={"reason": "explicit_terminate"},
    )
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "execution is terminated"
    assert adapter.invocation_count == 0


def test_expired_mount_does_not_invoke_adapter(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path, ttl_seconds=1)
    time.sleep(1.1)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "execution token has expired"
    assert adapter.invocation_count == 0


def test_missing_target_action_mapping_is_denied(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path, CreateOnlyAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, action="record.delete"),
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "effect_not_mapped"
    assert adapter.invocation_count == 0


def test_failure_before_write_is_failed(client: TestClient, tmp_path: Path) -> None:
    mount, adapter = prepare(client, tmp_path, FailingAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert body["decision"] == "allow"
    assert body["consequence"]["state"] == "failed"
    assert not (tmp_path / "activation-1.json").exists()
    assert adapter.invocation_count == 1


def test_uncertain_after_write_is_unknown_and_file_exists(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path, UncertainAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert body["decision"] == "allow"
    assert body["consequence"]["state"] == "outcome_unknown"
    assert (tmp_path / "activation-1.json").exists()
    assert adapter.invocation_count == 1


def test_operation_id_mismatch_is_denied(client: TestClient, tmp_path: Path) -> None:
    mount, adapter = prepare(client, tmp_path)
    first = execute_payload(mount, operation_id="operation-intent")
    client.post(f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=first)
    second = execute_payload(
        mount,
        operation_id="operation-intent",
        resource="different-record",
    )
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=second
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "idempotency_intent_mismatch"
    assert adapter.invocation_count == 1


def test_traversal_is_rejected_without_invocation(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="../admin"),
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "invalid_effect_resource"
    assert adapter.invocation_count == 0
    assert not (tmp_path.parent / "admin.json").exists()
