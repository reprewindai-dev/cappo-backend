"""Sandbox and live are one product with the same rules, but sandbox never reaches live state.

The Capability OS sandbox mounts under ``execution_scope.project = "sandbox"``. Targets that
key their state by project (the governed counter) keep sandbox apart on their own. A target
with a single dataset (an HTTP record store such as the arena) cannot, so CAPPO refuses a
sandbox consequence there instead of changing records live users see (found 2026-10-08).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.effects import LocalRecordAdapter, TargetAdapterRegistry
from cappo_backend.capability_mount.http_target import HttpTargetAdapter
from cappo_backend.capability_mount.service import SANDBOX_PROJECT
from tests.capability_mount.test_terminate_fence import ConfirmedAnchor, execute_payload, records_package


class IsolatedRecords(LocalRecordAdapter):
    """A record target that keeps a separate copy per project."""

    project_isolated = True


def _setup(client: TestClient, adapter: LocalRecordAdapter) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(LocalRecordAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"


def _mount(client: TestClient, project: str) -> dict:
    return client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": project},
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    ).json()


def _execute(client: TestClient, body: dict) -> dict:
    payload = execute_payload(body, "op-sandbox-1")
    return client.post(f"/v1/capability/mounts/{body['mount']['id']}/execute", json=payload).json()


def test_sandbox_consequence_on_a_single_dataset_target_is_refused_and_never_sent(
    client: TestClient, tmp_path: Path
) -> None:
    target = LocalRecordAdapter(tmp_path / "records")
    _setup(client, target)
    body = _mount(client, SANDBOX_PROJECT)
    assert body["decision"] == "allow"

    result = _execute(client, body)

    assert (result["decision"], result["reason"]) == ("deny", "target_has_no_sandbox")
    assert result["consequence"]["target_invoked"] is False
    assert target.invocation_count == 0


def test_sandbox_consequence_runs_on_a_project_isolated_target(client: TestClient, tmp_path: Path) -> None:
    target = IsolatedRecords(tmp_path / "records")
    _setup(client, target)

    result = _execute(client, _mount(client, SANDBOX_PROJECT))

    assert result["decision"] == "allow" and result["consequence"]["state"] == "succeeded"
    assert target.invocation_count == 1


def test_live_project_on_a_single_dataset_target_is_unaffected(client: TestClient, tmp_path: Path) -> None:
    target = LocalRecordAdapter(tmp_path / "records")
    _setup(client, target)

    result = _execute(client, _mount(client, "p1"))

    assert result["decision"] == "allow" and result["consequence"]["state"] == "succeeded"


def test_http_record_stores_declare_no_sandbox_copy() -> None:
    assert HttpTargetAdapter.project_isolated is False
