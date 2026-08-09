from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def package() -> CapabilityPackage:
    return CapabilityPackage(
        id="outreach@v1",
        family="outreach",
        title="Governed Outreach",
        purpose="Send approved external outreach",
        reads=["contact.read"],
        writes=["draft.write", "outreach.email_send"],
        blocked=["credential.export"],
        outputs=["draft"],
        policy_defaults={"mode": "draft_only"},
        external_send_actions=["outreach.email_send"],
        suppression_required_actions=["outreach.email_send"],
    )


def prepare(client: TestClient, anchor: ConfirmedAnchor | None = None) -> ConfirmedAnchor:
    selected = anchor or ConfirmedAnchor()
    registry = client.app.state.mount_registry
    registry.register_package(package())
    registry.anchor = selected
    registry._anchor_bound = True
    return selected


def mount_payload(ttl_seconds: int = 300) -> dict[str, object]:
    return {
        "package_ref": "outreach@v1",
        "execution_scope": {"workspace": "w1", "project": "p1"},
        "requested_action_scope": {
            "reads": ["contact.read"],
            "writes": ["draft.write", "outreach.email_send"],
            "blocked": ["draft.write"],
        },
        "role": "ephemeral_executor",
        "policy": {"mode": "draft_only"},
        "ttl_seconds": ttl_seconds,
    }


def test_mount_lifecycle_and_ttl_cap(client: TestClient) -> None:
    anchor = prepare(client)
    response = client.post("/v1/capability/mounts", json=mount_payload(9999))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    assert body["mount"]["token"]["ttl_seconds"] == 600
    assert body["token"]["ttl_seconds"] == 600
    assert body["anchoring"]["status"] == "confirmed"

    mount_id = body["mount"]["id"]
    status = client.get(f"/v1/capability/mounts/{mount_id}")
    assert status.json()["decision"] == "allow"

    blocked = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": body["token"]["token_id"],
            "nonce": body["token"]["nonce"],
            "action": "draft.write",
        },
    )
    assert blocked.json()["decision"] == "deny"
    assert blocked.json()["reason"] == "blocked_action"

    allowed = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": body["token"]["token_id"],
            "nonce": body["token"]["nonce"],
            "action": "contact.read",
        },
    )
    assert allowed.json()["decision"] == "allow"

    terminated = client.post(
        f"/v1/capability/mounts/{mount_id}/terminate",
        json={"reason": "explicit_terminate"},
    )
    assert terminated.json()["decision"] == "allow"
    after = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": body["token"]["token_id"],
            "nonce": body["token"]["nonce"],
            "action": "contact.read",
        },
    )
    assert after.json()["decision"] == "deny"
    assert after.json()["reason"] == "terminated"
    assert [event["event_type"] for event in anchor.events] == [
        "mount",
        "action_decision",
        "action_decision",
        "terminate",
        "action_decision",
    ]


def test_unknown_package_mount_is_governed_deny(client: TestClient) -> None:
    anchor = prepare(client)
    payload = mount_payload()
    payload["package_ref"] = "missing@v1"
    response = client.post("/v1/capability/mounts", json=payload)
    assert response.status_code == 200
    assert response.json()["decision"] == "deny"
    assert response.json()["reason"] == "unknown_package"
    assert anchor.events[-1]["decision"] == "deny"


def test_unknown_mount_and_pgl_failure_are_not_allows(client: TestClient) -> None:
    prepare(client, ConfirmedAnchor("unconfirmed"))
    response = client.post("/v1/capability/mounts", json=mount_payload())
    assert response.json()["decision"] == "deny"
    assert response.json()["reason"] == "pgl_anchor_unconfirmed"

    unknown = client.post(
        "/v1/capability/mounts/mnt_missing/actions",
        json={"token_id": "missing", "nonce": "missing", "action": "contact.read"},
    )
    assert unknown.json()["decision"] == "deny"
    assert unknown.json()["reason"] == "unknown_mount"


def test_sequential_mounts_use_live_sessions(client: TestClient) -> None:
    anchor = prepare(client)
    first = client.post("/v1/capability/mounts", json=mount_payload())
    second = client.post("/v1/capability/mounts", json=mount_payload())
    assert first.json()["decision"] == "allow"
    assert second.json()["decision"] == "allow"
    assert [event["event_type"] for event in anchor.events] == ["mount", "mount"]


def test_expired_mount_denies(client: TestClient) -> None:
    anchor = prepare(client)
    response = client.post("/v1/capability/mounts", json=mount_payload(1))
    body = response.json()
    record = client.app.state.mount_registry.get(body["mount"]["id"])
    assert record is not None
    now = datetime.now(timezone.utc)
    record.token = record.token.model_copy(
        update={"issued_at": now - timedelta(seconds=2), "expires_at": now - timedelta(seconds=1)}
    )
    record.binding.token = record.token
    denied = client.post(
        f"/v1/capability/mounts/{body['mount']['id']}/actions",
        json={
            "token_id": body["token"]["token_id"],
            "nonce": body["token"]["nonce"],
            "action": "contact.read",
        },
    )
    assert denied.json()["decision"] == "deny"
    assert denied.json()["reason"] == "token_expired"
    assert anchor.events[-1]["decision"] == "deny"
