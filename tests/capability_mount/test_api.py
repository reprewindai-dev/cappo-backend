from __future__ import annotations

import time

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
    assert body["token"]["nonce_consumed"] is False
    assert body["anchoring"]["status"] == "confirmed"
    assert "detail" not in body["anchoring"]

    mount_id = body["mount"]["id"]
    status = client.get(f"/v1/capability/mounts/{mount_id}")
    assert status.json()["decision"] == "allow"
    assert status.json()["token"] is None
    assert status.json()["nonce_consumed"] is False

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
    status_after_action = client.get(f"/v1/capability/mounts/{mount_id}")
    assert status_after_action.json()["token"] is None
    assert status_after_action.json()["nonce_consumed"] is True

    replay = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": body["token"]["token_id"],
            "nonce": body["token"]["nonce"],
            "action": "contact.read",
        },
    )
    assert replay.json()["decision"] == "deny"
    assert replay.json()["reason"] == "token_replay"
    status_after_replay = client.get(f"/v1/capability/mounts/{mount_id}")
    assert status_after_replay.json()["token"] is None
    assert status_after_replay.json()["nonce_consumed"] is True

    terminated = client.post(
        f"/v1/capability/mounts/{mount_id}/terminate",
        json={"reason": "explicit_terminate"},
    )
    assert terminated.json()["decision"] == "allow"
    status_after_terminate = client.get(f"/v1/capability/mounts/{mount_id}")
    assert status_after_terminate.json()["decision"] == "deny"
    assert status_after_terminate.json()["reason"] == "terminated"
    assert status_after_terminate.json()["token"] is None
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
        "action_decision",
        "terminate",
        "action_decision",
    ]


def test_capability_lease_is_consumed_by_governed_execution_and_replay_is_denied(
    client: TestClient,
) -> None:
    prepare(client)
    mounted = client.post("/v1/capability/mounts", json=mount_payload())
    assert mounted.status_code == 200
    lease = mounted.json()
    mount_id = lease["mount"]["id"]
    authority = {
        "mount_id": mount_id,
        "token_id": lease["token"]["token_id"],
        "nonce": lease["token"]["nonce"],
    }
    execution = {
        "prompt": "read the approved contact",
        "pgl_id": "test-user-id",
        "directive": "ALLOW",
        "action": "contact.read",
        "scope": {"tools": ["contact.read"], "allowed_effects": ["contact.read"]},
        "capability_lease": authority,
    }

    allowed = client.post("/v1/exec", json=execution)

    assert allowed.status_code == 200
    assert allowed.json()["execution_id"]
    assert allowed.json()["capability_lease"]["mount_id"] == mount_id
    assert allowed.json()["capability_lease"]["decision"] == "allow"

    replay = client.post("/v1/exec", json=execution)

    assert replay.status_code == 403
    assert replay.json()["detail"]["error"] == "CAPABILITY_LEASE_DENIED"
    assert replay.json()["detail"]["reason"] == "token_replay"


def test_governed_execution_denies_egress_outside_the_capability_lease(
    client: TestClient,
) -> None:
    prepare(client)
    lease = client.post("/v1/capability/mounts", json=mount_payload()).json()
    response = client.post(
        "/v1/exec",
        json={
            "prompt": "send data to an unapproved destination",
            "pgl_id": "test-user-id",
            "directive": "ALLOW",
            "action": "network.egress:unapproved.example:443",
            "scope": {"tools": ["network.egress:unapproved.example:443"]},
            "capability_lease": {
                "mount_id": lease["mount"]["id"],
                "token_id": lease["token"]["token_id"],
                "nonce": lease["token"]["nonce"],
            },
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "CAPABILITY_LEASE_DENIED"
    assert response.json()["detail"]["reason"] == "not_in_capability_profile"


def test_write_capability_requires_an_observed_target_precondition(client: TestClient) -> None:
    prepare(client)
    payload = mount_payload()
    payload["requested_action_scope"]["blocked"] = []
    lease = client.post("/v1/capability/mounts", json=payload).json()
    response = client.post(
        "/v1/exec",
        json={
            "prompt": "write a draft",
            "pgl_id": "test-user-id",
            "directive": "ALLOW",
            "action": "draft.write",
            "scope": {"tools": ["draft.write"], "allowed_effects": ["draft.write"]},
            "capability_lease": {
                "mount_id": lease["mount"]["id"],
                "token_id": lease["token"]["token_id"],
                "nonce": lease["token"]["nonce"],
            },
        },
    )

    assert response.status_code == 428
    assert response.json()["detail"]["error"] == "TARGET_PRECONDITION_REQUIRED"


def test_raw_approval_and_suppression_assertions_fail_closed(client: TestClient) -> None:
    prepare(client)
    response = client.post("/v1/capability/mounts", json=mount_payload())
    body = response.json()
    assert body["decision"] == "allow"

    attempted_send = client.post(
        f"/v1/capability/mounts/{body['mount']['id']}/actions",
        json={
            "token_id": body["token"]["token_id"],
            "nonce": body["token"]["nonce"],
            "action": "outreach.email_send",
            "approval_token": "arbitrary-caller-string",
            "suppression_confirmed": True,
        },
    )
    assert attempted_send.json()["decision"] == "deny"
    assert attempted_send.json()["reason"] == "human_approval_not_verified"

    status = client.get(f"/v1/capability/mounts/{body['mount']['id']}")
    assert status.json()["token"] is None
    assert status.json()["nonce_consumed"] is False


def test_authenticated_mount_is_bound_to_principal_and_workspace(
    client: TestClient,
    settings,
) -> None:
    prepare(client)
    settings.auth_enabled = True
    settings.api_keys = "owner-key,other-key"
    client.headers["X-API-Key"] = "owner-key"
    client.headers["X-Workspace-ID"] = "w1"

    mounted = client.post("/v1/capability/mounts", json=mount_payload())
    assert mounted.status_code == 200
    body = mounted.json()
    assert body["decision"] == "allow"

    client.headers["X-API-Key"] = "other-key"
    wrong_principal = client.get(f"/v1/capability/mounts/{body['mount']['id']}")
    assert wrong_principal.json()["decision"] == "deny"
    assert wrong_principal.json()["reason"] == "owner_mismatch"
    assert wrong_principal.json()["mount"] is None
    assert wrong_principal.json()["token"] is None

    client.headers["X-API-Key"] = "owner-key"
    client.headers["X-Workspace-ID"] = "other-workspace"
    wrong_workspace = client.get(f"/v1/capability/mounts/{body['mount']['id']}")
    assert wrong_workspace.json()["decision"] == "deny"
    assert wrong_workspace.json()["reason"] == "owner_mismatch"

    mismatched_creation = client.post("/v1/capability/mounts", json=mount_payload())
    assert mismatched_creation.status_code == 403
    assert mismatched_creation.json()["detail"] == "WORKSPACE_SCOPE_MISMATCH"


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


def test_expired_mount_status_is_not_live(client: TestClient) -> None:
    prepare(client)
    response = client.post("/v1/capability/mounts", json=mount_payload(1))
    body = response.json()
    time.sleep(1.1)
    status = client.get(f"/v1/capability/mounts/{body['mount']['id']}")
    assert status.json()["decision"] == "deny"
    assert status.json()["reason"] == "expired"
    assert status.json()["token"] is None
