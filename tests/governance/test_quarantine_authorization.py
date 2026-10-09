"""Authorization proofs for the governance v2 quarantine controls.

Audit 2026-10-09 found three defects on ``/v1/governance/v2/quarantine``:

1. ``approve`` took ``approver_trust`` and ``approver_id`` from the body and a
   client header, so any authenticated caller passed the trust gate.
2. ``deny`` had no authorization beyond authentication.
3. The queue listed every workspace's items (requester ids leaked cross-tenant).

These tests pin the fixed behaviour at the HTTP boundary with the same
credential shape the website gateway uses: a LockerPhycer-minted JWT whose
``sub`` is the account and whose ``workspace_id`` is the workspace that
account owns. Trust comes only from the server-held approver registry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from cappo_backend.config import Settings
from cappo_backend.main import create_app
from cappo_backend.services.governance_approvers import (
    ApproverResolutionError,
    resolve_approver,
)
from cappo_backend.services.mcp_v2 import get_mcp_v2_stack, reset_mcp_v2_stack
from cappo_backend.services.safety import AnomalyDetection, QuarantinedRequest

JWT_SECRET = "governance-v2-test-jwt-secret-0123456789abcdef"  # >= 32 bytes for HS256
JWT_ISSUER = "veklom-lockerphycer"
JWT_AUDIENCE = "veklom-cappo"

ALICE = "alice@veklom.com"  # registered approver, trust 95
BOB = "bob@veklom.com"  # registered approver, default trust (100)
CAROL = "carol@veklom.com"  # registered approver, trust 90
DAVE = "dave@veklom.com"  # signed-in account, NOT an approver
WS_A = "ws-a"
WS_B = "ws-b"

QUEUE = "/v1/governance/v2/quarantine"


@pytest.fixture(autouse=True)
def _reset_stack() -> Any:
    reset_mcp_v2_stack()
    yield
    reset_mcp_v2_stack()


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        api_keys="test-key",
        environment="development",
        auth_enabled=True,
        jwt_public_verification_key=JWT_SECRET,
        jwt_algorithm="HS256",
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
        governance_approvers=f"{ALICE}:95,{BOB},{CAROL}:90",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(_settings()))


def _bearer(subject: str, workspace: str | None) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    if workspace is not None:
        payload["workspace_id"] = workspace
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _critical_anomaly(agent_id: str) -> AnomalyDetection:
    return AnomalyDetection(
        detection_id=f"det-{agent_id}",
        agent_id=agent_id,
        detected_at=datetime.now(timezone.utc),
        anomaly_type="request_spike",
        deviation_score=9.0,
        anomaly_score=95.0,
        severity="critical",
        recommended_action="block",
        evidence_hash="deadbeef",
    )


def _quarantine(requester: str, workspace: str | None) -> QuarantinedRequest:
    stack = get_mcp_v2_stack()
    return stack.quarantine.quarantine(
        {"agent_id": requester, "operation": "privileged_transfer"},
        [_critical_anomaly(requester)],
        workspace_id=workspace,
    )


def _approve(client: TestClient, qid: str, headers: dict[str, str], body: Any = None) -> Any:
    url = f"{QUEUE}/{qid}/approve"
    if body is None:
        return client.post(url, headers=headers)
    return client.post(url, headers=headers, json=body)


def _deny(client: TestClient, qid: str, headers: dict[str, str], reason: str = "operator rejected") -> Any:
    return client.post(f"{QUEUE}/{qid}/deny", headers=headers, json={"reason": reason})


# ---------------------------------------------------------------------------
# Trust can only come from the server
# ---------------------------------------------------------------------------


def test_self_declared_trust_is_rejected_by_schema(client: TestClient) -> None:
    qr = _quarantine("requester-1", WS_A)

    resp = _approve(client, qr.quarantine_id, _bearer(DAVE, WS_A), {"approver_trust": 100})
    assert resp.status_code == 422, resp.text
    assert qr.approvals_received == []
    assert qr.status == "quarantined"

    # Even a registered approver cannot send a trust value.
    resp = _approve(client, qr.quarantine_id, _bearer(ALICE, WS_A), {"approver_trust": 100})
    assert resp.status_code == 422, resp.text
    assert qr.approvals_received == []


def test_unregistered_caller_cannot_approve(client: TestClient) -> None:
    qr = _quarantine("requester-1", WS_A)

    for body in (None, {}):
        resp = _approve(client, qr.quarantine_id, _bearer(DAVE, WS_A), body)
        assert resp.status_code == 403, resp.text
        assert "APPROVER_NOT_AUTHORIZED" in resp.json()["detail"]
    assert qr.approvals_received == []
    assert qr.status == "quarantined"


def test_client_headers_cannot_name_the_approver(client: TestClient) -> None:
    qr = _quarantine("requester-1", WS_A)

    headers = {**_bearer(DAVE, WS_A), "X-Authenticated-Agent-Id": ALICE}
    resp = _approve(client, qr.quarantine_id, headers, {})
    assert resp.status_code == 403, resp.text
    assert "APPROVER_NOT_AUTHORIZED" in resp.json()["detail"]
    assert qr.approvals_received == []


def test_body_approver_id_cannot_impersonate(client: TestClient) -> None:
    qr = _quarantine("requester-1", WS_A)

    resp = _approve(client, qr.quarantine_id, _bearer(ALICE, WS_A), {"approver_id": BOB})
    assert resp.status_code == 403, resp.text
    assert "APPROVER_IDENTITY_MISMATCH" in resp.json()["detail"]
    assert qr.approvals_received == []

    # A body approver_id that matches the verified identity is accepted.
    resp = _approve(client, qr.quarantine_id, _bearer(ALICE, WS_A), {"approver_id": ALICE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["approver_id"] == ALICE
    assert qr.approvals_received == [ALICE]


def test_registered_approvers_reach_quorum_with_server_held_trust(client: TestClient) -> None:
    qr = _quarantine("requester-1", WS_A)
    assert qr.approvers_required == 2

    first = _approve(client, qr.quarantine_id, _bearer(ALICE, WS_A), {})
    assert first.status_code == 200, first.text
    assert first.json() == {
        "quorum_reached": False,
        "status": "quarantined",
        "approver_id": ALICE,
        "approvals_received": [ALICE],
    }

    second = _approve(client, qr.quarantine_id, _bearer(BOB, WS_A))
    assert second.status_code == 200, second.text
    assert second.json()["quorum_reached"] is True
    assert second.json()["status"] == "approved"
    assert qr.approvals_received == [ALICE, BOB]

    # The recorded approvers are the verified subjects, nothing else.
    listed = client.get(QUEUE, headers=_bearer(ALICE, WS_A)).json()["items"]
    item = next(i for i in listed if i["quarantine_id"] == qr.quarantine_id)
    assert item["approvals_received"] == [ALICE, BOB]
    assert item["status"] == "approved"


# ---------------------------------------------------------------------------
# Self-approval stays forbidden
# ---------------------------------------------------------------------------


def test_self_approval_forbidden_even_for_registered_approver(client: TestClient) -> None:
    qr = _quarantine(ALICE, WS_A)
    assert qr.requester_id == ALICE

    resp = _approve(client, qr.quarantine_id, _bearer(ALICE, WS_A), {})
    assert resp.status_code == 403, resp.text
    assert "SELF_APPROVAL_FORBIDDEN" in resp.json()["detail"]
    assert ALICE in resp.json()["detail"]
    assert qr.approvals_received == []
    assert qr.status == "quarantined"

    # Casing tricks on the registered identity do not help either.
    resp = _approve(client, qr.quarantine_id, _bearer(ALICE.upper(), WS_A), {})
    assert resp.status_code == 403, resp.text
    assert "SELF_APPROVAL_FORBIDDEN" in resp.json()["detail"]
    assert qr.approvals_received == []

    # Independent registered approvers still reach quorum.
    assert _approve(client, qr.quarantine_id, _bearer(BOB, WS_A), {}).status_code == 200
    done = _approve(client, qr.quarantine_id, _bearer(CAROL, WS_A), {})
    assert done.status_code == 200, done.text
    assert done.json()["quorum_reached"] is True
    assert qr.approvals_received == [BOB, CAROL]


# ---------------------------------------------------------------------------
# Workspace scoping
# ---------------------------------------------------------------------------


def test_queue_lists_only_the_callers_workspace(client: TestClient) -> None:
    in_a = _quarantine("requester-in-a", WS_A)
    in_b = _quarantine("requester-in-b", WS_B)
    unbound = _quarantine("requester-unbound", None)

    resp = client.get(QUEUE, headers=_bearer(ALICE, WS_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == WS_A
    assert [i["quarantine_id"] for i in body["items"]] == [in_a.quarantine_id]
    assert "requester-in-b" not in resp.text
    assert in_b.quarantine_id not in resp.text
    assert unbound.quarantine_id not in resp.text

    resp = client.get(QUEUE, headers=_bearer(DAVE, WS_B))
    assert resp.status_code == 200, resp.text
    assert [i["quarantine_id"] for i in resp.json()["items"]] == [in_b.quarantine_id]
    assert "requester-in-a" not in resp.text


def test_queue_requires_a_workspace_bound_credential(client: TestClient) -> None:
    _quarantine("requester-in-a", WS_A)

    no_workspace = client.get(QUEUE, headers=_bearer(ALICE, None))
    assert no_workspace.status_code == 403, no_workspace.text
    assert "WORKSPACE_CONTEXT_MISSING" in no_workspace.json()["detail"]

    # API keys carry no server-side workspace binding; a header cannot supply one.
    api_key = client.get(QUEUE, headers={"X-API-Key": "test-key", "X-Workspace-ID": WS_A})
    assert api_key.status_code == 403, api_key.text
    assert "WORKSPACE_CONTEXT_MISSING" in api_key.json()["detail"]

    # A JWT whose workspace claim contradicts the header is refused by the middleware.
    mismatch = client.get(QUEUE, headers={**_bearer(ALICE, WS_A), "X-Workspace-ID": WS_B})
    assert mismatch.status_code == 403, mismatch.text


def test_cross_workspace_approve_and_deny_are_refused(client: TestClient) -> None:
    in_b = _quarantine("requester-in-b", WS_B)
    unbound = _quarantine("requester-unbound", None)

    for qid in (in_b.quarantine_id, unbound.quarantine_id):
        approve = _approve(client, qid, _bearer(ALICE, WS_A), {})
        assert approve.status_code == 404, approve.text
        deny = _deny(client, qid, _bearer(ALICE, WS_A))
        assert deny.status_code == 404, deny.text

    assert in_b.status == "quarantined" and in_b.approvals_received == []
    assert unbound.status == "quarantined" and unbound.approvals_received == []

    # Same identity, right workspace: the item is reachable.
    ok = _approve(client, in_b.quarantine_id, _bearer(ALICE, WS_B), {})
    assert ok.status_code == 200, ok.text
    assert in_b.approvals_received == [ALICE]


# ---------------------------------------------------------------------------
# Deny authority
# ---------------------------------------------------------------------------


def test_registered_approver_can_deny_in_own_workspace(client: TestClient) -> None:
    qr = _quarantine("requester-1", WS_A)

    resp = _deny(client, qr.quarantine_id, _bearer(CAROL, WS_A), "operator rejected")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "denied", "resolved_by": CAROL}
    assert qr.status == "denied"
    assert qr.resolution_reason == "operator rejected"

    # Already resolved: a second deny is a conflict, not a silent overwrite.
    again = _deny(client, qr.quarantine_id, _bearer(ALICE, WS_A), "again")
    assert again.status_code == 409, again.text
    assert qr.resolved_by == CAROL


def test_workspace_owner_can_deny_but_not_approve() -> None:
    """A session bound to the request's workspace is its owner (LockerPhycer only
    issues the workspace claim for the workspace the account owns). The owner may
    deny, but approval still requires registry trust."""
    client = TestClient(create_app(_settings(governance_approvers="")))
    owner = "owner@veklom.com"
    qr = _quarantine("requester-1", WS_A)

    approve = _approve(client, qr.quarantine_id, _bearer(owner, WS_A), {})
    assert approve.status_code == 403, approve.text
    assert "APPROVER_NOT_AUTHORIZED" in approve.json()["detail"]
    assert qr.approvals_received == []

    # The owner of another workspace is not this request's owner.
    other = _quarantine("requester-2", WS_B)
    assert _deny(client, other.quarantine_id, _bearer(owner, WS_A)).status_code == 404
    assert other.status == "quarantined"

    deny = _deny(client, qr.quarantine_id, _bearer(owner, WS_A), "owner rejected")
    assert deny.status_code == 200, deny.text
    assert qr.status == "denied"
    assert qr.resolved_by == owner


def test_caller_without_approver_or_owner_authority_cannot_deny() -> None:
    """With authentication disabled the principal is the ``auth-disabled`` sentinel:
    it is neither a registered approver nor a session-bound workspace owner."""
    client = TestClient(create_app(Settings(api_keys="test-key", environment="development")))
    qr = _quarantine("requester-1", "default-local-workspace")

    deny = client.post(f"{QUEUE}/{qr.quarantine_id}/deny", json={"reason": "nope"})
    assert deny.status_code == 403, deny.text
    assert "DENY_NOT_AUTHORIZED" in deny.json()["detail"]
    assert qr.status == "quarantined"

    approve = client.post(f"{QUEUE}/{qr.quarantine_id}/approve", json={})
    assert approve.status_code == 403, approve.text
    assert "APPROVER_NOT_AUTHORIZED" in approve.json()["detail"]
    assert qr.approvals_received == []


# ---------------------------------------------------------------------------
# Resolver and registry units
# ---------------------------------------------------------------------------


def _request_with_scope(**scope: Any) -> Request:
    base: dict[str, Any] = {"type": "http", "method": "POST", "path": "/", "headers": []}
    base.update(scope)
    return Request(base)


def test_resolver_uses_only_the_verified_scope() -> None:
    settings = _settings()

    with pytest.raises(ApproverResolutionError) as unauthenticated:
        resolve_approver(_request_with_scope(), settings)
    assert unauthenticated.value.status_code == 401

    with pytest.raises(ApproverResolutionError) as holder:
        resolve_approver(
            _request_with_scope(auth_principal="mount-holder:m-1", auth_workspace=WS_A), settings
        )
    assert holder.value.status_code == 403
    assert holder.value.code == "HOLDER_SCOPE_FORBIDDEN"

    # Headers naming an approver are not part of the scope the resolver reads.
    ctx = resolve_approver(
        _request_with_scope(
            auth_principal=f"jwt:{JWT_ISSUER}:{DAVE}",
            jwt_payload={"sub": DAVE, "workspace_id": WS_A},
            auth_workspace=WS_A,
            headers=[(b"x-authenticated-agent-id", ALICE.encode())],
        ),
        settings,
    )
    assert ctx.approver_id == DAVE
    assert ctx.registered is False and ctx.trust == 0.0
    assert ctx.may_approve is False
    assert ctx.owns_workspace(WS_A) is True and ctx.owns_workspace(WS_B) is False

    api_key = resolve_approver(
        _request_with_scope(auth_principal="api-key:abc123", auth_workspace=None),
        _settings(governance_approvers="api-key:abc123:85"),
    )
    assert api_key.approver_id == "api-key:abc123"
    assert api_key.trust == 85.0 and api_key.may_approve is True
    # API keys never own a workspace, even if one were in scope.
    assert api_key.owns_workspace(WS_A) is False


def test_registry_parses_identities_and_trust() -> None:
    registry = _settings(
        governance_approvers=f" {ALICE}:95 , {BOB}, api-key:{'7' * 64}, spiffe://veklom/ops/eve:70, weak@veklom.com:10,"
    ).governance_approver_registry
    assert registry[ALICE] == 95.0
    assert registry[BOB] == 100.0
    assert registry[f"api-key:{'7' * 64}"] == 100.0  # digit-only fingerprint is not a trust suffix
    assert registry["spiffe://veklom/ops/eve"] == 70.0
    assert registry["weak@veklom.com"] == 10.0
    assert _settings(governance_approvers="").governance_approver_registry == {}
