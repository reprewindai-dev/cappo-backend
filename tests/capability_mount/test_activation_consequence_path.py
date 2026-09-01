from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

import cappo_backend.security.biscuit as biscuit
from cappo_backend.capability_mount.effects import EffectTargetRegistry, LocalRecordAdapter
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from tests.capability_mount.test_execute_consequence import (
    ConfirmedAnchor,
    execute_payload,
    prepare,
    records_package,
)


def _configure_root(
    monkeypatch: pytest.MonkeyPatch,
    settings,
    root_path: Path,
) -> None:
    settings.biscuit_root_private_key_hex = None
    settings.biscuit_root_key_path = str(root_path)
    monkeypatch.setattr(biscuit, "get_settings", lambda: settings)
    biscuit._ROOT_KEY_PAIR = None


def test_activation_create_persists_authority_and_lifecycle(
    client,
    db,
    settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_root(monkeypatch, settings, tmp_path / "biscuit-root")
    mount, adapter = prepare(client, tmp_path)
    biscuit_value = mount["token"]["biscuit_token"]
    assert biscuit_value

    persisted = db.execute(
        select(CapabilityMount).where(
            CapabilityMount.mount_id == mount["mount"]["id"]
        )
    ).scalar_one()
    persisted_biscuit = persisted.token_json["biscuit_token"]
    assert hashlib.sha256(biscuit_value.encode()).hexdigest() == hashlib.sha256(
        persisted_biscuit.encode()
    ).hexdigest()
    assert persisted_biscuit == biscuit_value

    other_directory = tmp_path / "other"
    other_directory.mkdir()
    biscuit._ROOT_KEY_PAIR = None
    monkeypatch.chdir(other_directory)
    authority = biscuit.extract_authority_context(persisted_biscuit)
    assert authority is not None
    assert authority.allowed_actions == {"record.create", "record.delete"}

    response = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(
            mount,
            resource="activation-proof",
            operation_id="activation-create",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    assert body["reason"] == "allowed"
    assert body["consequence"]["state"] == "succeeded"
    assert body["consequence"]["target_invoked"] is True
    assert body["consequence"]["terminated"] is True
    assert adapter.invocations_by_action["record.create"] == 1
    persisted_after = db.execute(
        select(CapabilityMount).where(
            CapabilityMount.mount_id == mount["mount"]["id"]
        )
    ).scalar_one()
    assert persisted_after.terminated is True
    assert json.loads((tmp_path / "activation-proof.json").read_text()) == {
        "status": "active",
        "attempt": 1,
    }

    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == "activation-create")
        .order_by(ConsequenceExecutionEvent.version)
    ).scalars().all()
    assert [event.state for event in events] == ["authorized", "started", "succeeded"]

    replay = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(
            mount,
            resource="activation-proof",
            operation_id="activation-create",
        ),
    )
    assert replay.status_code == 200
    replay_body = replay.json()
    assert replay_body["decision"] == "deny"
    assert replay_body["reason"] == "idempotency_replay:succeeded"
    assert adapter.invocations_by_action["record.create"] == 1


def test_activation_blocked_delete_preserves_target(
    client,
    tmp_path: Path,
) -> None:
    mount, adapter = prepare(client, tmp_path)
    record = tmp_path / "protected.json"
    record.write_bytes(b'{"status":"protected"}')
    before = record.read_bytes()

    response = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(
            mount,
            action="record.delete",
            resource="protected",
            operation_id="activation-blocked-delete",
        ),
    )
    body = response.json()
    assert body["decision"] == "deny"
    assert body["reason"] == "blocked_action"
    assert body["consequence"]["target_invoked"] is False
    assert body["consequence"]["terminated"] is True
    assert adapter.invocation_count == 0
    assert record.read_bytes() == before


def test_activation_verified_biscuit_scope_denial(
    client,
    db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    anchor = ConfirmedAnchor()
    registry.anchor = anchor
    adapter = LocalRecordAdapter(tmp_path)
    registry.effect_targets = EffectTargetRegistry()
    registry.effect_targets.register(LocalRecordAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"

    original_mint = biscuit.mint_biscuit_capability

    def narrower_biscuit(**kwargs: object) -> str:
        narrowed = dict(kwargs)
        narrowed["writes"] = []
        return original_mint(**narrowed)

    monkeypatch.setattr(biscuit, "mint_biscuit_capability", narrower_biscuit)
    mounted = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {
                "reads": ["record.read"],
                "writes": ["record.create"],
                "blocked": [],
            },
            "ttl_seconds": 300,
        },
    )
    assert mounted.status_code == 200
    mount = mounted.json()
    assert mount["decision"] == "allow"
    authority = biscuit.extract_authority_context(mount["token"]["biscuit_token"])
    assert authority is not None
    assert authority.allowed_actions == {"record.read"}

    response = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(
            mount,
            resource="authority-denied",
            operation_id="activation-authority-denial",
        ),
    )
    body = response.json()
    assert body["decision"] == "deny"
    assert body["reason"] == "lease_invariant_violation"
    assert body["reason"] not in {
        "missing_cryptographic_authority",
        "blocked_action",
        "not_in_capability_profile",
    }
    assert body["consequence"]["target_invoked"] is False
    assert body["consequence"]["terminated"] is True
    assert adapter.invocation_count == 0
    assert not (tmp_path / "authority-denied.json").exists()
    persisted = db.execute(
        select(CapabilityMount).where(
            CapabilityMount.mount_id == mount["mount"]["id"]
        )
    ).scalar_one()
    assert persisted.terminated is True
    decisions = [
        event for event in anchor.events if event["event_type"] == "action_decision"
    ]
    assert decisions
    assert decisions[-1]["decision"] == "deny"
    assert decisions[-1]["reason"] == "lease_invariant_violation"
