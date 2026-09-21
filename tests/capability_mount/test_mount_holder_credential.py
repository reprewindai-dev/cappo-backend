from __future__ import annotations

import re
import time

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from cappo_backend.capability_mount.effects import GovernedCounterAdapter
from cappo_backend.capability_mount.service import GOVERNED_COUNTER_PACKAGE


def _configure(client: TestClient, db: Session, tmp_path, monkeypatch) -> None:
    settings = client.app.state.settings
    settings.auth_enabled = True
    settings.api_keys = "owner-key"
    client.headers["X-API-Key"] = "owner-key"
    client.headers["X-Workspace-ID"] = "holder-workspace"

    registry = client.app.state.mount_registry
    registry.register_package(GOVERNED_COUNTER_PACKAGE)
    registry.target_adapters.register(
        GovernedCounterAdapter.ref,
        GovernedCounterAdapter(tmp_path),
    )
    monkeypatch.setattr(
        "cappo_backend.security.auth_middleware.SessionLocal",
        sessionmaker(bind=db.get_bind(), expire_on_commit=False),
    )


def _mount(client: TestClient, *, project: str = "sandbox", ttl_seconds: int = 300) -> dict:
    response = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": GOVERNED_COUNTER_PACKAGE.id,
            "execution_scope": {
                "workspace": "holder-workspace",
                "project": project,
            },
            "requested_action_scope": {
                "reads": ["counter.read"],
                "writes": ["counter.increment"],
                "blocked": ["counter.reset"],
            },
            "ttl_seconds": ttl_seconds,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow", body
    return body


def _holder_headers(client: TestClient, credential: str) -> None:
    client.headers.pop("X-API-Key", None)
    client.headers["Authorization"] = f"Bearer {credential}"
    client.headers["X-Workspace-ID"] = "holder-workspace"


def test_issuance_and_status_redaction(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    _configure(client, db, tmp_path, monkeypatch)
    mounted = _mount(client)

    credential = mounted["holder_credential"]
    assert re.fullmatch(r"vlm_.+\..+", credential)

    status = client.get(f"/v1/capability/mounts/{mounted['mount']['id']}")
    assert status.status_code == 200
    assert status.json()["holder_credential"] is None


def test_holder_can_status_action_execute_and_readback(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    _configure(client, db, tmp_path, monkeypatch)
    mounted = _mount(client)
    mount_id = mounted["mount"]["id"]
    token = mounted["token"]
    _holder_headers(client, mounted["holder_credential"])

    status = client.get(f"/v1/capability/mounts/{mount_id}")
    assert status.status_code == 200
    assert status.json()["reason"] == "mounted"

    readback = client.get(
        f"/v1/capability/targets/{GovernedCounterAdapter.ref}/state",
        params={"resource": "holder-counter", "mount_id": mount_id},
    )
    assert readback.status_code == 200
    assert readback.json()["project"] == "sandbox"
    assert readback.json()["state"]["value"] == 0

    blocked = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "counter.reset",
            "resource": "holder-counter",
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["decision"] == "deny"

    executed = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "counter.increment",
            "target_ref": GovernedCounterAdapter.ref,
            "resource": "holder-counter",
            "operation_id": "holder-counter-increment",
        },
    )
    assert executed.status_code == 200
    assert executed.json()["decision"] == "allow"

    post_execute_readback = client.get(
        f"/v1/capability/targets/{GovernedCounterAdapter.ref}/state",
        params={"resource": "holder-counter", "mount_id": mount_id},
    )
    assert post_execute_readback.status_code == 200
    assert post_execute_readback.json()["state"]["value"] == 1

    terminated_status = client.get(f"/v1/capability/mounts/{mount_id}")
    assert terminated_status.status_code == 200
    assert terminated_status.json()["reason"] == "terminated"

    blocked_after_termination = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "counter.reset",
            "resource": "holder-counter",
        },
    )
    assert blocked_after_termination.status_code == 401
    assert blocked_after_termination.json()["error"] == "HOLDER_CREDENTIAL_REVOKED"

    replay_after_termination = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "counter.increment",
            "target_ref": GovernedCounterAdapter.ref,
            "resource": "holder-counter",
            "operation_id": "holder-counter-increment",
        },
    )
    assert replay_after_termination.status_code == 401
    assert replay_after_termination.json()["error"] == "HOLDER_CREDENTIAL_REVOKED"


def test_holder_scope_forbidden_on_mount_and_packages(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    _configure(client, db, tmp_path, monkeypatch)
    mounted = _mount(client)
    _holder_headers(client, mounted["holder_credential"])

    mount_again = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": GOVERNED_COUNTER_PACKAGE.id,
            "execution_scope": {"workspace": "holder-workspace", "project": "other"},
        },
    )
    assert mount_again.status_code == 403
    assert mount_again.json()["detail"] == "HOLDER_SCOPE_FORBIDDEN"

    packages = client.get("/v1/capability/packages")
    assert packages.status_code == 403
    assert packages.json()["detail"] == "HOLDER_SCOPE_FORBIDDEN"


def test_tampered_holder_credential_is_rejected(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    _configure(client, db, tmp_path, monkeypatch)
    mounted = _mount(client)
    credential = mounted["holder_credential"]
    tampered = f"{credential[:-1]}x"
    _holder_headers(client, tampered)

    response = client.get(f"/v1/capability/mounts/{mounted['mount']['id']}")
    assert response.status_code == 401
    assert response.json()["error"] == "HOLDER_CREDENTIAL_INVALID"


def test_holder_is_bound_to_one_mount(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    _configure(client, db, tmp_path, monkeypatch)
    first = _mount(client, project="sandbox")
    second = _mount(client, project="production")
    _holder_headers(client, first["holder_credential"])

    response = client.get(f"/v1/capability/mounts/{second['mount']['id']}")
    assert response.status_code == 200
    assert response.json()["decision"] == "deny"
    assert response.json()["reason"] == "owner_mismatch"


def test_holder_credential_revoked_after_owner_termination(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    _configure(client, db, tmp_path, monkeypatch)
    mounted = _mount(client)
    credential = mounted["holder_credential"]
    mount_id = mounted["mount"]["id"]

    owner_termination = client.post(
        f"/v1/capability/mounts/{mount_id}/terminate",
        json={"reason": "explicit_terminate"},
    )
    assert owner_termination.status_code == 200
    assert owner_termination.json()["decision"] == "allow"

    _holder_headers(client, credential)
    response = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={
            "token_id": mounted["token"]["token_id"],
            "nonce": mounted["token"]["nonce"],
            "action": "counter.reset",
            "resource": "holder-counter",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "HOLDER_CREDENTIAL_REVOKED"


def test_expired_holder_credential_is_rejected(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    _configure(client, db, tmp_path, monkeypatch)
    mounted = _mount(client, ttl_seconds=1)
    _holder_headers(client, mounted["holder_credential"])
    time.sleep(1.1)

    status = client.get(f"/v1/capability/mounts/{mounted['mount']['id']}")
    assert status.status_code == 200
    assert status.json()["reason"] == "expired"

    response = client.post(
        f"/v1/capability/mounts/{mounted['mount']['id']}/actions",
        json={
            "token_id": mounted["token"]["token_id"],
            "nonce": mounted["token"]["nonce"],
            "action": "counter.reset",
            "resource": "holder-counter",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "HOLDER_CREDENTIAL_EXPIRED"
