import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

SLASH_PATH = "/api/internal/interlink/vnp/authorize-slash"
RELEASE_PATH = "/api/internal/interlink/vnp/authorize-release"
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


def _slash_payload() -> dict[str, str]:
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
    path: str = SLASH_PATH,
    method: str = "POST",
) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    message = "\n".join([method, path, timestamp, nonce, canonical])
    return hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def _signed_headers(
    payload: dict[str, str],
    *,
    path: str = SLASH_PATH,
    timestamp: str | None = None,
    nonce: str | None = None,
):
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    nonce = nonce or uuid.uuid4().hex
    return _base_headers() | {
        "x-vnp-signature": _signature(payload, timestamp, nonce, path=path),
        "x-vnp-timestamp": timestamp,
        "x-vnp-nonce": nonce,
    }


def _assert_unverified_denial(response, action: str) -> None:
    assert response.status_code == 503
    data = response.json()
    assert data["authorized"] is False
    assert data["action"] == action
    assert data["evidence_status"] == "NOT_VERIFIED"
    assert data["reason"] == "canonical_pgl_evidence_verifier_unavailable"
    assert "authorization_receipt" not in data


def test_authorize_slash_missing_signature(client):
    response = client.post(SLASH_PATH, headers=_base_headers(), json=_slash_payload())
    assert response.status_code == 401


def test_authorize_slash_invalid_signature(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _slash_payload()
    headers = _base_headers() | {
        "x-vnp-signature": "bad_signature",
        "x-vnp-timestamp": datetime.now(timezone.utc).isoformat(),
        "x-vnp-nonce": uuid.uuid4().hex,
    }
    response = client.post(SLASH_PATH, headers=headers, json=payload)
    assert response.status_code == 403


def test_authenticated_slash_still_denies_unverified_pgl_evidence(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _slash_payload()
    response = client.post(SLASH_PATH, headers=_signed_headers(payload), json=payload)

    _assert_unverified_denial(response, "slash")


def test_nonexistent_pgl_shaped_identifier_cannot_mint_receipt(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _slash_payload() | {"pgl_evidence_id": "pgl_definitely_nonexistent"}
    response = client.post(SLASH_PATH, headers=_signed_headers(payload), json=payload)

    _assert_unverified_denial(response, "slash")


def test_invalid_pgl_identifier_format_is_rejected(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _slash_payload() | {"pgl_evidence_id": "attacker-controlled"}
    response = client.post(SLASH_PATH, headers=_signed_headers(payload), json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid PGL evidence format"


def test_same_evidence_id_cannot_authorize_different_bond_or_challenge(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    evidence_id = "pgl_unverified_binding_probe"
    first_payload = _slash_payload() | {"pgl_evidence_id": evidence_id}
    second_payload = {
        **first_payload,
        "bond_id": str(uuid.uuid4()),
        "challenge_id": str(uuid.uuid4()),
    }

    first = client.post(SLASH_PATH, headers=_signed_headers(first_payload), json=first_payload)
    second = client.post(SLASH_PATH, headers=_signed_headers(second_payload), json=second_payload)

    _assert_unverified_denial(first, "slash")
    _assert_unverified_denial(second, "slash")


def test_release_also_fails_closed_without_canonical_evidence_verifier(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = {
        "bond_id": str(uuid.uuid4()),
        "pgl_evidence_id": "pgl_unverified_release",
    }
    response = client.post(
        RELEASE_PATH,
        headers=_signed_headers(payload, path=RELEASE_PATH),
        json=payload,
    )

    _assert_unverified_denial(response, "release")


def test_vnp_signature_cannot_be_reused_for_modified_body(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    original = _slash_payload()
    headers = _signed_headers(original)
    modified = dict(original, pgl_evidence_id="pgl_attacker_changed")

    response = client.post(SLASH_PATH, headers=headers, json=modified)

    assert response.status_code == 403


def test_vnp_signature_expires(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _slash_payload()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    response = client.post(
        SLASH_PATH,
        headers=_signed_headers(payload, timestamp=stale),
        json=payload,
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Expired VNP signature"


def test_vnp_nonce_is_single_use_even_when_evidence_is_denied(client, monkeypatch):
    monkeypatch.setenv("VNP_CAPPO_INTERLINK_SECRET", SECRET)
    payload = _slash_payload()
    timestamp = datetime.now(timezone.utc).isoformat()
    nonce = uuid.uuid4().hex
    headers = _signed_headers(payload, timestamp=timestamp, nonce=nonce)

    first = client.post(SLASH_PATH, headers=headers, json=payload)
    second = client.post(SLASH_PATH, headers=headers, json=payload)

    _assert_unverified_denial(first, "slash")
    assert second.status_code == 409
    assert second.json()["detail"] == "VNP request replay detected"
