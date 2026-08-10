import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone


PATH = "/api/internal/interlink/vnp/authorize-slash"
SECRET = "test-vnp-interlink-secret"


def _base_headers() -> dict[str, str]:
    return {
        "x-agent-id": "test-agent",
        "x-request-id": "test-req",
        "x-execution-identity": '{"principal": "test-principal"}',
        "x-capability-id": "test-cap",
        "x-target-url": "test-url",
        "x-payment": "BYPASS",
        "x-pgl-pre-cert": "test-cert",
    }


def _payload() -> dict[str, str]:
    return {
        "bond_id": str(uuid.uuid4()),
        "challenge_id": str(uuid.uuid4()),
        "pgl_evidence_id": "pgl_456",
    }


def _signature(
    payload: dict[str, str],
    timestamp: str,
    nonce: str,
    *,
    path: str = PATH,
    method: str = "POST",
) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    message = "\n".join([method, path, timestamp, nonce, canonical])
    return hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def _signed_headers(payload: dict[str, str], *, timestamp: str | None = None, nonce: str | None = None):
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    nonce = nonce or uuid.uuid4().hex
    return _base_headers() | {
        "x-vnp-signature": _signature(payload, timestamp, nonce),
        "x-vnp-timestamp": timestamp,
        "x-vnp-nonce": nonce,
    }


def test_authorize_slash_missing_signature(client):
    response = client.post(PATH, headers=_base_headers(), json=_payload())
    assert response.status_code == 401


def test_authorize_slash_invalid_signature(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _payload()
    headers = _base_headers() | {
        "x-vnp-signature": "bad_signature",
        "x-vnp-timestamp": datetime.now(timezone.utc).isoformat(),
        "x-vnp-nonce": uuid.uuid4().hex,
    }
    response = client.post(PATH, headers=headers, json=payload)
    assert response.status_code == 403


def test_authorize_slash_valid_body_bound_signature(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _payload()
    response = client.post(PATH, headers=_signed_headers(payload), json=payload)
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["authorized"] is True
    assert data["action"] == "slash"
    assert data["receipt_version"] == 2
    assert data["authorization_receipt"].startswith("cappo_auth_slash_v2_")


def test_vnp_signature_cannot_be_reused_for_modified_body(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    original = _payload()
    headers = _signed_headers(original)
    modified = dict(original, pgl_evidence_id="pgl_attacker_changed")

    response = client.post(PATH, headers=headers, json=modified)

    assert response.status_code == 403


def test_vnp_signature_expires(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _payload()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    response = client.post(PATH, headers=_signed_headers(payload, timestamp=stale), json=payload)

    assert response.status_code == 401
    assert response.json()["detail"] == "Expired VNP signature"


def test_vnp_nonce_is_single_use(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _payload()
    timestamp = datetime.now(timezone.utc).isoformat()
    nonce = uuid.uuid4().hex
    headers = _signed_headers(payload, timestamp=timestamp, nonce=nonce)

    first = client.post(PATH, headers=headers, json=payload)
    second = client.post(PATH, headers=headers, json=payload)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "VNP request replay detected"
