from __future__ import annotations

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.capability_mount.service import AnchorResult


class ConfirmedAnchor:
    def __init__(self, status: str = "confirmed") -> None:
        self.status = status
        self.events: list[dict[str, str]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.events.append({"event_type": event_type, **{k: str(v) for k, v in payload.items()}})
        return AnchorResult(self.status, anchor_id=f"anchor-{len(self.events)}")


def _package() -> CapabilityPackage:
    return CapabilityPackage(
        id="pgo-e2e@v1",
        family="pgo-e2e",
        title="PGO E2E Contract",
        purpose="Exercise one bounded Capability OS object through governed execution",
        reads=["contact.read"],
        writes=["draft.write"],
        blocked=["credential.export"],
        outputs=["draft"],
        policy_defaults={"mode": "draft_only"},
    )


def _prepare(client: TestClient) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(_package())
    registry.anchor = ConfirmedAnchor()


def _mount_payload() -> dict[str, object]:
    return {
        "package_ref": "pgo-e2e@v1",
        "execution_scope": {"workspace": "w1", "project": "p1"},
        "requested_action_scope": {
            "reads": ["contact.read"],
            "writes": ["draft.write"],
            "blocked": ["credential.export"],
        },
        "role": "ephemeral_executor",
        "policy": {"mode": "draft_only"},
        "ttl_seconds": 300,
    }


def _lease_from_mount(mounted: dict[str, object]) -> dict[str, str]:
    mount = mounted["mount"]
    token = mounted["token"]
    assert isinstance(mount, dict)
    assert isinstance(token, dict)
    return {
        "mount_id": str(mount["id"]),
        "token_id": str(token["token_id"]),
        "nonce": str(token["nonce"]),
    }


def test_governed_exec_consumes_backend_issued_lease_once(client: TestClient) -> None:
    _prepare(client)
    mounted_response = client.post("/v1/capability/mounts", json=_mount_payload())
    assert mounted_response.status_code == 200
    lease = _lease_from_mount(mounted_response.json())

    execution = {
        "prompt": "read the approved contact",
        "pgl_id": "test-user-id",
        "workspace_id": "w1",
        "directive": "ALLOW",
        "action": "contact.read",
        "scope": {"tools": ["contact.read"], "allowed_effects": ["contact.read"]},
        "capability_lease": lease,
    }

    allowed = client.post("/v1/exec", json=execution)
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["execution_id"]
    assert body["capability_lease"]["mount_id"] == lease["mount_id"]
    assert body["capability_lease"]["decision"] == "allow"

    replay = client.post("/v1/exec", json=execution)
    assert replay.status_code == 403
    assert replay.json()["detail"]["error"] == "CAPABILITY_LEASE_DENIED"
    assert replay.json()["detail"]["reason"] == "token_replay"


def test_governed_exec_denies_action_outside_backend_lease(client: TestClient) -> None:
    _prepare(client)
    mounted = client.post("/v1/capability/mounts", json=_mount_payload()).json()
    lease = _lease_from_mount(mounted)

    response = client.post(
        "/v1/exec",
        json={
            "prompt": "export credentials",
            "pgl_id": "test-user-id",
            "workspace_id": "w1",
            "directive": "ALLOW",
            "action": "credential.export",
            "scope": {"tools": ["credential.export"], "allowed_effects": ["credential.export"]},
            "capability_lease": lease,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "CAPABILITY_LEASE_DENIED"
    assert response.json()["detail"]["reason"] in {"blocked_action", "not_in_capability_profile"}


def test_governed_write_requires_target_precondition_before_execution(client: TestClient) -> None:
    _prepare(client)
    mounted = client.post("/v1/capability/mounts", json=_mount_payload()).json()
    lease = _lease_from_mount(mounted)

    response = client.post(
        "/v1/exec",
        json={
            "prompt": "write a draft",
            "pgl_id": "test-user-id",
            "workspace_id": "w1",
            "directive": "ALLOW",
            "action": "draft.write",
            "scope": {"tools": ["draft.write"], "allowed_effects": ["draft.write"]},
            "capability_lease": lease,
        },
    )

    assert response.status_code == 428
    assert response.json()["detail"]["error"] == "TARGET_PRECONDITION_REQUIRED"
