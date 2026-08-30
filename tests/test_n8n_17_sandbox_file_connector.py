from __future__ import annotations

import json
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from cappo_backend.execution.idempotency_registry import ExecutionState
from cappo_backend.execution.revocation_registry import LeaseState
from cappo_backend.execution.sandbox_file_connector import (
    ConnectorConflict,
    ConnectorDenied,
    SandboxFileAppendConnector,
)
from scripts.n8n_17_connector_target import create_app


def claims(connector: SandboxFileAppendConnector, execution_id: str = "exec-1") -> dict:
    return {
        "execution_id": execution_id,
        "allowed_actions": [connector.append_action],
        "allowed_resources": [connector.resource],
    }


def test_append_is_idempotent_and_reconciles(tmp_path: Path) -> None:
    connector = SandboxFileAppendConnector(tmp_path / "governed.jsonl")
    authority = claims(connector)

    first = connector.append(authority, "hello")
    duplicate = connector.append(authority, "hello")

    assert duplicate == first
    lines = connector.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["receipt"] == first.as_dict()
    assert connector.reconcile("exec-1", first.action_hash) is not None


def test_execution_id_cannot_change_action_data(tmp_path: Path) -> None:
    connector = SandboxFileAppendConnector(tmp_path / "governed.jsonl")
    authority = claims(connector)
    connector.append(authority, "first")

    with pytest.raises(ConnectorConflict):
        connector.append(authority, "different")


def test_scope_is_exact_and_wildcards_are_not_accepted(tmp_path: Path) -> None:
    connector = SandboxFileAppendConnector(tmp_path / "governed.jsonl")
    authority = claims(connector)
    authority["allowed_resources"] = ["sandbox:*"]

    with pytest.raises(ConnectorDenied, match="resource"):
        connector.append(authority, "hello")


def test_compensation_is_append_only_governed_tombstone(tmp_path: Path) -> None:
    connector = SandboxFileAppendConnector(tmp_path / "governed.jsonl")
    connector.append(claims(connector, "exec-original"), "hello")
    compensation_claims = {
        "execution_id": "exec-compensate",
        "allowed_actions": [connector.compensate_action],
        "allowed_resources": [connector.resource],
    }

    receipt = connector.compensate(compensation_claims, "exec-original")

    records = [json.loads(line) for line in connector.path.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["action"] == connector.append_action
    assert records[1]["action"] == connector.compensate_action
    assert receipt.compensates_execution_id == "exec-original"


class MemoryRegistry:
    def __init__(self) -> None:
        self.rows = {}

    def reserve(self, jti, execution_id, action_data):
        existing = self.rows.get(execution_id)
        if existing:
            if existing["jti"] != jti or existing["action"] != action_data:
                return False, "DENY: conflicting duplicate", None
            return False, None, existing.get("result")
        self.rows[execution_id] = {"jti": jti, "action": action_data}
        return True, None, None

    def update_state(self, execution_id, state: ExecutionState, result=None):
        self.rows[execution_id]["state"] = state.value
        if result:
            self.rows[execution_id]["result"] = result
        return True


class ActiveRevocations:
    def check_authority(self, kid, subject, lease_id, execution_id):
        return LeaseState.ACTIVE


def signed_token(private_key, kid, resource, execution_id="exec-http"):
    import time

    now = int(time.time())
    return jwt.encode(
        {
            "iss": "cappo.veklom.com",
            "aud": "sandbox_file_append",
            "sub": "workspace:test",
            "iat": now,
            "nbf": now,
            "exp": now + 60,
            "jti": f"jti-{execution_id}",
            "lease_id": f"lease-{execution_id}",
            "execution_id": execution_id,
            "allowed_actions": ["fs:append"],
            "allowed_resources": [resource],
        },
        private_key,
        algorithm="EdDSA",
        headers={"kid": kid},
    )


def test_http_worker_verifies_authority_and_returns_original_receipt(tmp_path: Path) -> None:
    target = tmp_path / "governed.jsonl"
    resource = f"sandbox-file:{target.resolve(strict=False).as_posix()}"
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    registry = MemoryRegistry()
    app = create_app(
        key_fetcher=lambda kid: public_bytes if kid == "kid-1" else None,
        target_path=str(target),
        registry=registry,
        revocations=ActiveRevocations(),
    )
    client = TestClient(app)
    token = signed_token(private_key, "kid-1", resource)
    headers = {"Authorization": f"Bearer {token}"}
    body = {"action": "fs:append", "content": "certified"}

    first = client.post("/connectors/sandbox-file-append", headers=headers, json=body)
    duplicate = client.post("/connectors/sandbox-file-append", headers=headers, json=body)

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert duplicate.headers["X-Veklom-Receipt-ID"] == first.headers["X-Veklom-Receipt-ID"]
    assert len(target.read_text().splitlines()) == 1


def test_http_worker_denies_wrong_resource(tmp_path: Path) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    app = create_app(
        key_fetcher=lambda kid: public_bytes,
        target_path=str(tmp_path / "governed.jsonl"),
        registry=MemoryRegistry(),
        revocations=ActiveRevocations(),
    )
    token = signed_token(private_key, "kid-1", "sandbox-file:/tmp/not-authorized")
    response = TestClient(app).post(
        "/connectors/sandbox-file-append",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "fs:append", "content": "denied"},
    )

    assert response.status_code == 403
    assert "resource" in response.json()["detail"]
