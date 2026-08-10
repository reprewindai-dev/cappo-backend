from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import CapabilityPackage, Mount
from cappo_backend.capability_mount.service import AnchorResult
from cappo_backend.models.capability_evidence_consumption import CapabilityEvidenceConsumption
from cappo_backend.services.mount_evidence import issue_bound_mount_evidence

APPROVAL_KEY = "test-approval-evidence-signing-key"
SUPPRESSION_KEY = "test-suppression-evidence-signing-key"
ACTION = "outreach.email_send"


class Anchor:
    def __init__(self, status: str = "confirmed") -> None:
        self.status = status

    def anchor(self, event_type: str, **_: object) -> AnchorResult:
        return AnchorResult(self.status, anchor_id=f"{self.status}-{event_type}")


def _package() -> CapabilityPackage:
    return CapabilityPackage(
        id="evidence-outreach@v1",
        family="evidence-outreach",
        title="Evidence-bound outreach",
        purpose="Exercise signed mount evidence gates",
        writes=[ACTION],
        policy_defaults={"mode": "draft_only"},
        external_send_actions=[ACTION],
        suppression_required_actions=[ACTION],
    )


def _mount(client: TestClient) -> dict:
    client.app.state.mount_registry.register_package(_package())
    client.app.state.mount_registry.anchor = Anchor("confirmed")
    response = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "evidence-outreach@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"writes": [ACTION]},
            "ttl_seconds": 300,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    assert body["ttl_seconds"] == 300
    assert body["expires_at"] == body["token"]["expires_at"]
    return body


def _evidence(body: dict, *, approval_key: str = APPROVAL_KEY, nonce: str | None = None):
    mount = Mount.model_validate(body["mount"])
    bound_nonce = nonce or body["token"]["nonce"]
    approval = issue_bound_mount_evidence(
        kind="human_approval",
        signing_key=approval_key,
        principal="auth-disabled",
        mount=mount,
        action=ACTION,
        nonce=bound_nonce,
    )
    suppression = issue_bound_mount_evidence(
        kind="suppression_check",
        signing_key=SUPPRESSION_KEY,
        principal="auth-disabled",
        mount=mount,
        action=ACTION,
        nonce=bound_nonce,
    )
    return approval, suppression


def _action(client: TestClient, body: dict, approval: str, suppression: str):
    return client.post(
        f"/v1/capability/mounts/{body['mount']['id']}/actions",
        json={
            "token_id": body["token"]["token_id"],
            "nonce": body["token"]["nonce"],
            "action": ACTION,
            "approval_token": approval,
            "suppression_evidence": suppression,
        },
    )


def test_signed_bound_approval_and_suppression_allow_once(
    client: TestClient,
    settings,
    db: Session,
    monkeypatch,
) -> None:
    settings.approval_token_signing_key = APPROVAL_KEY
    monkeypatch.setenv("SUPPRESSION_EVIDENCE_SIGNING_KEY", SUPPRESSION_KEY)
    body = _mount(client)
    approval, suppression = _evidence(body)

    allowed = _action(client, body, approval, suppression)

    assert allowed.status_code == 200
    assert allowed.json()["decision"] == "allow"
    consumed = list(db.execute(select(CapabilityEvidenceConsumption)).scalars())
    assert sorted(item.kind for item in consumed) == ["human_approval", "suppression_check"]

    replay = _action(client, body, approval, suppression)
    assert replay.json()["decision"] == "deny"
    assert replay.json()["reason"] == "token_replay"

    status = client.get(f"/v1/capability/mounts/{body['mount']['id']}").json()
    assert status["token"] is None
    assert status["ttl_seconds"] == body["token"]["ttl_seconds"]
    assert status["expires_at"] == body["token"]["expires_at"]
    assert status["nonce_consumed"] is True


def test_wrong_key_or_nonce_bound_evidence_fails_closed(
    client: TestClient,
    settings,
    monkeypatch,
) -> None:
    settings.approval_token_signing_key = APPROVAL_KEY
    monkeypatch.setenv("SUPPRESSION_EVIDENCE_SIGNING_KEY", SUPPRESSION_KEY)
    body = _mount(client)
    wrong_key_approval, suppression = _evidence(body, approval_key="wrong-key")

    wrong_key = _action(client, body, wrong_key_approval, suppression)
    assert wrong_key.json()["decision"] == "deny"
    assert wrong_key.json()["reason"] == "human_approval_not_verified"

    wrong_nonce_approval, wrong_nonce_suppression = _evidence(body, nonce="attacker-nonce")
    wrong_nonce = _action(client, body, wrong_nonce_approval, wrong_nonce_suppression)
    assert wrong_nonce.json()["decision"] == "deny"
    assert wrong_nonce.json()["reason"] == "human_approval_not_verified"

    status = client.get(f"/v1/capability/mounts/{body['mount']['id']}").json()
    assert status["nonce_consumed"] is False


def test_pgl_failure_rolls_back_evidence_consumption_and_nonce(
    client: TestClient,
    settings,
    db: Session,
    monkeypatch,
) -> None:
    settings.approval_token_signing_key = APPROVAL_KEY
    monkeypatch.setenv("SUPPRESSION_EVIDENCE_SIGNING_KEY", SUPPRESSION_KEY)
    body = _mount(client)
    approval, suppression = _evidence(body)

    client.app.state.mount_registry.anchor = Anchor("unconfirmed")
    denied = _action(client, body, approval, suppression)
    assert denied.json()["decision"] == "deny"
    assert denied.json()["reason"] == "pgl_anchor_unconfirmed"
    assert list(db.execute(select(CapabilityEvidenceConsumption)).scalars()) == []
    status = client.get(f"/v1/capability/mounts/{body['mount']['id']}").json()
    assert status["nonce_consumed"] is False

    client.app.state.mount_registry.anchor = Anchor("confirmed")
    retry = _action(client, body, approval, suppression)
    assert retry.json()["decision"] == "allow"
