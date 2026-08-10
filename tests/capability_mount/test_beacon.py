from __future__ import annotations

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.models import CapabilityPackage


def test_signed_beacon_round_trip_and_tamper_detection(client: TestClient) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(
        CapabilityPackage(
            id="outreach@v1",
            family="outreach",
            title="Governed Outreach",
            purpose="Send approved external outreach",
            policy_defaults={"mode": "draft_only"},
        )
    )
    response = client.get("/v1/capability/beacons/outreach@v1")
    assert response.status_code == 200
    beacon = response.json()
    assert response.headers["cache-control"].startswith("public, max-age=")
    assert "signature" in beacon
    assert "issuer_public_key" in beacon
    assert beacon["kid"] == "default"
    assert "price" not in beacon
    assert "trust_score" not in beacon

    verified = client.post("/v1/capability/beacons/verify", json={"beacon": beacon})
    assert verified.json() == {
        "valid": True,
        "reason": "verified",
        "verified_kid": beacon["kid"],
    }

    tampered = dict(beacon, policy_hash="tampered")
    invalid = client.post("/v1/capability/beacons/verify", json={"beacon": tampered})
    assert invalid.json() == {
        "valid": False,
        "reason": "signature_invalid",
        "verified_kid": None,
    }

    keys = client.get("/.well-known/capability-beacon-keys")
    assert keys.status_code == 200
    assert keys.json()["keys"][0]["kid"] == beacon["kid"]
    assert keys.json()["keys"][0]["public_key"] == beacon["issuer_public_key"]


def test_expired_beacon_rejected(client: TestClient) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(
        CapabilityPackage(
            id="expired@v1",
            family="expired",
            title="Expired",
            purpose="Expiry verification",
        )
    )
    beacon = client.get("/v1/capability/beacons/expired@v1").json()
    beacon["expires_at"] = "2020-01-01T00:00:00+00:00"
    rejected = client.post("/v1/capability/beacons/verify", json={"beacon": beacon})
    assert rejected.json() == {
        "valid": False,
        "reason": "beacon_expired",
        "verified_kid": None,
    }


def test_unknown_beacon_kid_is_not_reported_as_verified(client: TestClient) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(
        CapabilityPackage(
            id="unknown-kid@v1",
            family="unknown-kid",
            title="Unknown kid",
            purpose="Unknown kid verification",
        )
    )
    beacon = client.get("/v1/capability/beacons/unknown-kid@v1").json()
    beacon["kid"] = "attacker-selected-kid"

    rejected = client.post("/v1/capability/beacons/verify", json={"beacon": beacon})

    assert rejected.json() == {
        "valid": False,
        "reason": "unknown_kid",
        "verified_kid": None,
    }


def test_beacon_keys_are_public_when_authentication_is_enabled(
    client: TestClient,
    settings,
) -> None:
    settings.auth_enabled = True
    client.headers.pop("X-API-Key", None)

    response = client.get("/.well-known/capability-beacon-keys")

    assert response.status_code == 200
    assert response.json()["keys"][0]["kid"] == "default"
