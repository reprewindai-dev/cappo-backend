"""The evidence signing key must outlive containers and be publicly checkable (2026-10-11)."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

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


def _raw(pk):
    return pk.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )


def test_production_refuses_to_mint_a_new_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ev.EvidenceKeyUnavailable):
        ev.get_evidence_key_pair()


def test_production_rejects_malformed_env_key_instead_of_replacing_it(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("EVIDENCE_ROOT_PRIVATE_KEY_HEX", "not-hex")
    with pytest.raises(ev.EvidenceKeyUnavailable):
        ev.get_evidence_key_pair()


def test_legacy_container_key_is_migrated_to_the_volume_and_keeps_signing(tmp_path, monkeypatch):
    legacy_key = ed25519.Ed25519PrivateKey.generate()
    legacy = tmp_path / "legacy" / ".evidence_root_key"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(_raw(legacy_key))
    monkeypatch.setenv("ENVIRONMENT", "production")

    key = ev.get_evidence_key_pair()
    assert _raw(key) == _raw(legacy_key), "the key that signed earlier receipts must be kept"
    persisted = tmp_path / "vol" / "evidence_root_key"
    assert persisted.read_bytes() == _raw(legacy_key)

    # A replaced container has no legacy file; the volume copy must be enough.
    legacy.unlink()
    ev.reset_evidence_key_cache()
    assert _raw(ev.get_evidence_key_pair()) == _raw(legacy_key)


def test_env_key_wins_and_is_used_for_receipts(monkeypatch):
    env_key = ed25519.Ed25519PrivateKey.generate()
    monkeypatch.setenv("EVIDENCE_ROOT_PRIVATE_KEY_HEX", _raw(env_key).hex())
    cose = ev.mint_signed_execution_evidence({"execution_id": "exec_1", "decision": 0})
    assert ev.verify_signed_execution_evidence(cose, env_key.public_key())["execution_id"] == "exec_1"


def test_published_key_verifies_receipts_and_kid_names_the_key():
    import cbor2

    cose = ev.mint_signed_execution_evidence({"execution_id": "exec_2", "decision": 0})
    descriptor = ev.evidence_public_key_descriptor()
    raw_pub = base64.urlsafe_b64decode(descriptor["x"] + "=" * (-len(descriptor["x"]) % 4))
    published = ed25519.Ed25519PublicKey.from_public_bytes(raw_pub)

    assert ev.verify_signed_execution_evidence(cose, published)["execution_id"] == "exec_2"
    unprotected = cbor2.loads(cose).value[1]
    assert unprotected[4].decode() == descriptor["kid"]
    assert descriptor["kid"].startswith("ed25519-sha256:")

    stranger = ed25519.Ed25519PrivateKey.generate()
    forged = ev.mint_signed_execution_evidence({"execution_id": "exec_2", "decision": 0}, private_key=stranger)
    with pytest.raises(ValueError):
        ev.verify_signed_execution_evidence(forged, published)


def test_well_known_route_publishes_key_and_reports_unavailable(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cappo_backend.api.routers.evidence_key_router import evidence_key_router

    app = FastAPI()
    app.include_router(evidence_key_router)
    client = TestClient(app)

    ok = client.get("/.well-known/evidence-keys")
    assert ok.status_code == 200
    body = ok.json()
    assert body["keys"][0]["kid"] == ev.evidence_public_key_descriptor()["kid"]
    assert "d" not in body["keys"][0], "the private key must never be published"

    ev.reset_evidence_key_cache()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("EVIDENCE_ROOT_KEY_PATH", "/nonexistent-dir-for-test/evidence_root_key")
    down = client.get("/.well-known/evidence-keys")
    assert down.status_code == 503
    assert down.json()["status"] == "EVIDENCE_KEY_UNAVAILABLE"
