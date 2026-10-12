"""The owner can fetch a receipt's signed bytes and verify them with only the published key.

GET /v1/capability/receipts/{receipt_id} returns the COSE_Sign1 bytes and no verdict. The
check below uses nothing from CAPPO except the two HTTP responses: the receipt bundle and
/.well-known/evidence-keys (2026-10-11, "check it yourself" demo).
"""

from __future__ import annotations

import base64
import hashlib
import json

import cbor2
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.capability_mount.service import AnchorResult
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.security import evidence as ev


@pytest.fixture(autouse=True)
def isolated_key_env(tmp_path, monkeypatch):
    monkeypatch.delenv("EVIDENCE_ROOT_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.setenv("EVIDENCE_ROOT_KEY_PATH", str(tmp_path / "vol" / "evidence_root_key"))
    monkeypatch.setattr(ev, "LEGACY_KEY_PATHS", (str(tmp_path / "legacy" / ".evidence_root_key"),))
    monkeypatch.setenv("ENVIRONMENT", "development")
    ev.reset_evidence_key_cache()
    yield
    ev.reset_evidence_key_cache()


class _Anchor:
    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id="anchor-1")


def _install(client: TestClient) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(
        CapabilityPackage(
            id="outreach@v1",
            family="outreach",
            title="Governed Outreach",
            purpose="Send approved external outreach",
            reads=["contact.read"],
            writes=["draft.write"],
            blocked=["credential.export"],
            outputs=["draft"],
            policy_defaults={"mode": "draft_only"},
        )
    )
    registry.anchor = _Anchor()


def _receipt(client: TestClient, db: Session, headers: dict[str, str]) -> CapabilityActionReceipt:
    mount = client.post(
        "/v1/capability/mounts",
        headers=headers,
        json={
            "package_ref": "outreach@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"reads": ["contact.read"], "writes": [], "blocked": []},
            "role": "ephemeral_executor",
            "policy": {"mode": "draft_only"},
            "ttl_seconds": 300,
        },
    ).json()
    assert mount["decision"] == "allow", mount
    action = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/actions",
        headers=headers,
        json={
            "token_id": mount["token"]["token_id"],
            "nonce": mount["token"]["nonce"],
            "action": "contact.read",
        },
    ).json()
    assert action["decision"] == "allow", action
    rows = db.execute(
        select(CapabilityActionReceipt).where(
            CapabilityActionReceipt.mount_id == mount["mount"]["id"]
        )
    ).scalars().all()
    assert len(rows) == 1
    return rows[0]


def _verify(bundle: dict, keys: dict, cose: bytes | None = None) -> dict:
    """Third-party check: only the bundle and the published JWK are used."""
    cose = cose if cose is not None else base64.b64decode(bundle["cose_b64"])
    message = cbor2.loads(cose)
    assert isinstance(message, cbor2.CBORTag) and message.tag == 18
    protected, unprotected, payload, signature = message.value
    assert cbor2.loads(protected) == {1: -8}

    (jwk,) = keys["keys"]
    raw = base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
    fingerprint = "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()
    assert jwk["kid"] == fingerprint
    assert unprotected[4].decode() == fingerprint == bundle["kid"]

    sig_structure = cbor2.dumps(["Signature1", protected, b"", payload], canonical=True)
    ed25519.Ed25519PublicKey.from_public_bytes(raw).verify(signature, sig_structure)

    fields = cbor2.loads(payload)
    recomputed = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert recomputed == bundle["content_hash"]
    return fields


def test_owner_fetches_signed_receipt_and_verifies_it_with_the_published_key(
    client: TestClient, db: Session
) -> None:
    _install(client)
    headers = {"X-Workspace-ID": "ws-owner"}
    row = _receipt(client, db, headers)

    resp = client.get(f"/v1/capability/receipts/{row.receipt_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    bundle = resp.json()
    assert bundle["receipt_id"] == row.receipt_id
    assert bundle["mount_id"] == row.mount_id
    assert bundle["signed"] is True
    assert bundle["key_endpoint"] == "/.well-known/evidence-keys"
    assert "verdict" not in bundle and "valid" not in bundle, "the route must not vouch for itself"

    keys = client.get("/.well-known/evidence-keys").json()
    fields = _verify(bundle, keys)
    assert fields.get("receipt_schema_version") == row.receipt_schema_version
    assert fields["mount_id"] == row.mount_id
    assert fields["action"] == "contact.read"
    assert fields["decision"] == "allow"


@pytest.mark.parametrize("part", [2, 3], ids=["payload", "signature"])
def test_one_flipped_byte_fails_verification(client: TestClient, db: Session, part: int) -> None:
    _install(client)
    headers = {"X-Workspace-ID": "ws-owner"}
    row = _receipt(client, db, headers)
    bundle = client.get(f"/v1/capability/receipts/{row.receipt_id}", headers=headers).json()
    keys = client.get("/.well-known/evidence-keys").json()

    message = cbor2.loads(base64.b64decode(bundle["cose_b64"]))
    parts = list(message.value)
    target = bytearray(parts[part])
    target[len(target) // 2] ^= 0x01
    parts[part] = bytes(target)
    tampered = cbor2.dumps(cbor2.CBORTag(18, parts))

    with pytest.raises((InvalidSignature, AssertionError, cbor2.CBORDecodeError, ValueError)):
        _verify(bundle, keys, cose=tampered)


def test_receipt_of_another_principal_or_workspace_is_reported_as_not_found(
    client: TestClient, db: Session, settings
) -> None:
    # With auth disabled every mount belongs to "auth-disabled"; ownership is only
    # meaningful with auth on, as in production.
    _install(client)
    settings.auth_enabled = True
    settings.api_keys = "owner-key,other-key"
    client.headers["X-API-Key"] = "owner-key"
    owner = {"X-Workspace-ID": "w1"}  # the mount payload binds execution_scope.workspace=w1
    row = _receipt(client, db, owner)
    assert client.get(f"/v1/capability/receipts/{row.receipt_id}", headers=owner).status_code == 200

    missing = client.get("/v1/capability/receipts/rcpt_does_not_exist", headers=owner)

    other_workspace = client.get(
        f"/v1/capability/receipts/{row.receipt_id}", headers={"X-Workspace-ID": "ws-other"}
    )
    client.headers["X-API-Key"] = "other-key"
    other_principal = client.get(f"/v1/capability/receipts/{row.receipt_id}", headers=owner)

    for resp in (missing, other_workspace, other_principal):
        assert resp.status_code == 404, resp.text
        assert resp.json() == {"detail": "RECEIPT_NOT_FOUND"}
