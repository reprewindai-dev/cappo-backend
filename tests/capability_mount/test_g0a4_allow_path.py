from __future__ import annotations

"""G0A.4 — Prove the positive ALLOW path."""

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.models import CapabilityPackage, EphemeralScopedToken
from cappo_backend.capability_mount.service import AnchorResult


class _StructuredAnchor:
    def __init__(self, status: str = "confirmed") -> None:
        self.status = status
        self.raw_events: list[dict[str, object]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.raw_events.append({"event_type": event_type, **payload})
        return AnchorResult(self.status, anchor_id=f"anchor-{len(self.raw_events)}")


def _package() -> CapabilityPackage:
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


def _mount_payload(ttl_seconds: int = 300) -> dict[str, object]:
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


def _install(client: TestClient, anchor: _StructuredAnchor) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(_package())
    registry.anchor = anchor


def test_g0a_4_positive_allow_path(client: TestClient) -> None:
    from datetime import datetime as _dt

    spy = _StructuredAnchor(status="confirmed")
    _install(client, spy)

    # Mount
    mount_resp = client.post("/v1/capability/mounts", json=_mount_payload(300))
    assert mount_resp.status_code == 200, mount_resp.text
    mount_body = mount_resp.json()
    assert mount_body["decision"] == "allow"

    mount_id = mount_body["mount"]["id"]
    token_id = mount_body["token"]["token_id"]
    nonce = mount_body["token"]["nonce"]

    # Execute one action
    action_resp = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={"token_id": token_id, "nonce": nonce, "action": "contact.read"},
    )
    assert action_resp.status_code == 200, action_resp.text
    action_body = action_resp.json()
    assert action_body["decision"] == "allow", f"expected allow, got {action_body}"

    # Exactly one allow anchor event
    allow_events = [
        e for e in spy.raw_events
        if e["event_type"] == "action_decision" and e.get("decision") == "allow"
    ]
    assert len(allow_events) == 1, f"expected 1 allow anchor, got {len(allow_events)}"
    ev = allow_events[0]

    # Binding assertions
    principal = ev.get("principal")
    assert principal not in (None, ""), f"SUBJECT_BOUND FAIL: {ev}"
    assert ev.get("action") == "contact.read", f"ACTION_BOUND FAIL: {ev}"
    assert ev.get("decision") == "allow", f"DECISION_BOUND FAIL: {ev}"
    reason = ev.get("reason")
    assert reason not in (None, ""), f"REASON_BOUND FAIL: {ev}"
    ts = ev.get("timestamp")
    assert ts not in (None, ""), f"TIMESTAMP_BOUND FAIL: {ev}"
    _dt.fromisoformat(str(ts))
    assert ev.get("mount_id") == mount_id, f"MOUNT_ID_BOUND FAIL: {ev}"
    token_obj = ev.get("token")
    assert isinstance(token_obj, EphemeralScopedToken), f"TOKEN_BOUND FAIL: {type(token_obj)}"
    assert token_obj.token_id == token_id, f"TOKEN_ID_BOUND FAIL: {token_obj.token_id!r}"

    # Nonce consumed
    status_body = client.get(f"/v1/capability/mounts/{mount_id}").json()
    assert status_body["nonce_consumed"] is True, f"EXACTLY_ONCE FAIL: nonce not consumed: {status_body}"
    assert status_body["token"] is None, "EXACTLY_ONCE FAIL: token still exposed"

    # Replay denied
    replay_body = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={"token_id": token_id, "nonce": nonce, "action": "contact.read"},
    ).json()
    assert replay_body["decision"] == "deny", f"REPLAY FAIL: {replay_body}"
    assert replay_body["reason"] == "token_replay", f"REPLAY REASON FAIL: {replay_body}"

    # Still exactly one allow after replay
    allow_after = [e for e in spy.raw_events if e["event_type"] == "action_decision" and e.get("decision") == "allow"]
    assert len(allow_after) == 1, f"EXACTLY_ONCE FAIL: allow grew to {len(allow_after)}"

    # No ghost executions
    execution_events = [e for e in spy.raw_events if e["event_type"] == "execution"]
    assert len(execution_events) == 0, f"NO_GHOST FAIL: {execution_events}"

    print()
    print("G0A.4 = VERIFIED")
    print(f"SUBJECT_BOUND      = {principal!r}")
    print(f"ACTION_BOUND       = {ev['action']!r}")
    print(f"DECISION_BOUND     = {ev['decision']!r}")
    print(f"REASON_BOUND       = {reason!r}")
    print(f"TIMESTAMP_BOUND    = {ts!r}")
    print(f"EXECUTION_ID_BOUND = mount_id={mount_id!r}")
    print(f"TOKEN_ID_BOUND     = token_id={token_id!r}")
    print("ANCHOR_CONFIRMED   = True")
    print("NONCE_CONSUMED     = True")
    print("REPLAY_DENIED      = True (reason=token_replay)")
    print("EXACTLY_ONE_ALLOW  = True")
    print("NO_GHOST_EXECUTION = True (0 execution-type anchor events)")
