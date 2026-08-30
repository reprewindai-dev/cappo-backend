from __future__ import annotations

"""Regression: an expired capability must be denied at the action endpoint
before any governed side effect. Pairs with test_engine.py::test_ttl_expiry_denies
(raises TokenExpiredError) and test_api.py::test_expired_mount_status_is_not_live
(status path returns 'expired'). This test closes the HTTP-level gap."""

import time

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.capability_mount.service import AnchorResult


class ConfirmedAnchor:
    def __init__(self, status: str = "confirmed") -> None:
        self.status = status
        self.events: list[dict[str, str]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.events.append(
            {"event_type": event_type, **{k: str(v) for k, v in payload.items()}}
        )
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


def test_expired_capability_is_denied_at_action_boundary(client: TestClient) -> None:
    """Expired capability must be denied at the action endpoint before any side effect."""
    anchor = prepare(client)
    response = client.post("/v1/capability/mounts", json=mount_payload(1))
    body = response.json()
    mount_id = body["mount"]["id"]
    token_id = body["token"]["token_id"]
    nonce = body["token"]["nonce"]

    time.sleep(1.1)

    action = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={"token_id": token_id, "nonce": nonce, "action": "contact.read"},
    )

    payload = action.json()
    assert action.status_code == 200
    assert payload["decision"] == "deny"
    assert payload["reason"] == "token_expired"
    print("ANCHOR EVENTS:", anchor.events)

    deny_anchors = [e for e in anchor.events if e["event_type"] == "action_decision"]
    assert deny_anchors, "expected deny anchor to be recorded before side effect"
    deny = deny_anchors[-1]
    assert deny["decision"] == "deny"
    assert deny["reason"] == "token_expired"
    assert deny["principal"] == "auth-disabled"
    assert deny["action"] == "contact.read"
    assert deny["mount_id"] == mount_id
    assert "timestamp" in deny

    allow_anchors = [e for e in anchor.events if e["event_type"] == "action_decision" and e["decision"] == "allow"]
    assert len(allow_anchors) == 0

    execution_events = [e for e in anchor.events if e["event_type"] == "execution"]
    assert len(execution_events) == 0

    status = client.get(f"/v1/capability/mounts/{mount_id}")
    assert status.json()["decision"] == "deny"
    assert status.json()["reason"] == "expired"
    assert status.json()["token"] is None


# ---------------------------------------------------------------------------
# G0.3b — Denial evidence binding
# ---------------------------------------------------------------------------

class _StructuredAnchor:
    """Spy that preserves the raw kwarg values (not stringified) for G0.3b assertions."""

    def __init__(self, status: str = "confirmed") -> None:
        self.status = status
        self.raw_events: list[dict[str, object]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.raw_events.append({"event_type": event_type, **payload})
        return AnchorResult(self.status, anchor_id=f"anchor-{len(self.raw_events)}")


def test_g0_3b_denial_evidence_binding(client: TestClient) -> None:
    """G0.3b: expired capability denial evidence must bind all 6 required fields.

    Required bindings per the G0.3b gate:
    - subject / principal
    - requested capability or action
    - decision = deny
    - reason = token_expired
    - timestamp
    - execution identifier (mount_id, which is the canonical execution scope ID)

    Also asserts:
    - no ALLOW evidence record exists
    - no execution evidence record exists
    - no side effect occurs (mount status remains expired, token is consumed/absent)
    """
    from cappo_backend.capability_mount.models import EphemeralScopedToken

    spy = _StructuredAnchor(status="confirmed")

    # Register package and install spy anchor
    registry = client.app.state.mount_registry
    registry.register_package(package())
    registry.anchor = spy

    # Issue a mount that expires in 1 s
    resp = client.post("/v1/capability/mounts", json=mount_payload(1))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    mount_id = body["mount"]["id"]
    token_id = body["token"]["token_id"]
    nonce = body["token"]["nonce"]

    # Let it expire
    time.sleep(1.1)

    # Attempt the action on the expired capability
    action_resp = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={"token_id": token_id, "nonce": nonce, "action": "contact.read"},
    )
    assert action_resp.status_code == 200, action_resp.text
    action_body = action_resp.json()

    # ── HTTP response is deny ───────────────────────────────────────────────
    assert action_body["decision"] == "deny", f"expected deny, got {action_body}"
    assert action_body["reason"] == "token_expired"

    # ── Extract the deny anchor event ───────────────────────────────────────
    deny_events = [
        e for e in spy.raw_events
        if e["event_type"] == "action_decision" and e.get("decision") == "deny"
    ]
    assert deny_events, "No deny anchor event was recorded"
    ev = deny_events[-1]

    # ── G0.3b field assertions ──────────────────────────────────────────────

    # 1. Subject / principal
    assert ev.get("principal") not in (None, ""), (
        f"SUBJECT_BOUND FAIL — 'principal' missing or empty in evidence: {ev}"
    )
    subject = ev["principal"]

    # 2. Requested action
    assert ev.get("action") == "contact.read", (
        f"ACTION_BOUND FAIL — expected 'contact.read', got {ev.get('action')!r}"
    )

    # 3. Decision = deny
    assert ev.get("decision") == "deny", (
        f"DECISION_BOUND FAIL — expected 'deny', got {ev.get('decision')!r}"
    )

    # 4. Reason = token_expired
    assert ev.get("reason") == "token_expired", (
        f"REASON_BOUND FAIL — expected 'token_expired', got {ev.get('reason')!r}"
    )

    # 5. Timestamp (non-empty ISO string)
    ts = ev.get("timestamp")
    assert ts not in (None, ""), f"TIMESTAMP_BOUND FAIL — 'timestamp' missing: {ev}"
    # Confirm it is a parseable ISO-8601 datetime
    from datetime import datetime as _dt
    _dt.fromisoformat(str(ts))  # raises ValueError if malformed

    # 6. Execution identifier (mount_id = the canonical execution scope bound to this action)
    assert ev.get("mount_id") == mount_id, (
        f"EXECUTION_ID_BOUND FAIL — expected mount_id={mount_id!r}, got {ev.get('mount_id')!r}"
    )

    # Additionally: the token object should be present and carry the matching token_id
    token_obj = ev.get("token")
    assert isinstance(token_obj, EphemeralScopedToken), (
        f"EXECUTION_ID_BOUND FAIL — 'token' is not an EphemeralScopedToken: {type(token_obj)}"
    )
    assert token_obj.token_id == token_id, (
        f"EXECUTION_ID_BOUND FAIL — token.token_id mismatch: {token_obj.token_id!r} != {token_id!r}"
    )

    # ── No ALLOW evidence ──────────────────────────────────────────────────
    allow_events = [
        e for e in spy.raw_events
        if e["event_type"] == "action_decision" and e.get("decision") == "allow"
    ]
    assert len(allow_events) == 0, (
        f"NO_ALLOW_EVIDENCE FAIL — unexpected ALLOW anchor events: {allow_events}"
    )

    # ── No execution evidence ──────────────────────────────────────────────
    execution_events = [
        e for e in spy.raw_events if e["event_type"] == "execution"
    ]
    assert len(execution_events) == 0, (
        f"NO_EXECUTION_EVIDENCE FAIL — unexpected execution events: {execution_events}"
    )

    # ── No side effect: mount status unchanged, token absent ───────────────
    status_resp = client.get(f"/v1/capability/mounts/{mount_id}")
    status_body = status_resp.json()
    assert status_body["decision"] == "deny", (
        f"NO_SIDE_EFFECT FAIL — mount decision changed: {status_body}"
    )
    assert status_body["reason"] == "expired", (
        f"NO_SIDE_EFFECT FAIL — mount reason changed: {status_body}"
    )
    assert status_body["token"] is None, (
        f"NO_SIDE_EFFECT FAIL — token not None after expired denial: {status_body}"
    )

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    print("G0.3b = VERIFIED")
    print(f"SUBJECT_BOUND      = {subject!r}")
    print(f"ACTION_BOUND       = {ev['action']!r}")
    print(f"DECISION_BOUND     = {ev['decision']!r}")
    print(f"REASON_BOUND       = {ev['reason']!r}")
    print(f"TIMESTAMP_BOUND    = {ts!r}")
    print(f"EXECUTION_ID_BOUND = mount_id={mount_id!r} token_id={token_id!r}")
    print("NO_ALLOW_EVIDENCE  = True (0 allow events)")
    print("NO_EXECUTION_EVIDENCE = True (0 execution events)")
    print("NO_SIDE_EFFECT     = True (status=deny/expired, token=None)")
