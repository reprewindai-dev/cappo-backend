from __future__ import annotations

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.models import CapabilityPackage


def register_package(client: TestClient, package_id: str = "outreach@v1") -> None:
    family = package_id.rsplit("@", 1)[0]
    client.app.state.mount_registry.register_package(
        CapabilityPackage(
            id=package_id,
            family=family,
            title="Governed Capability",
            purpose="Beacon verification",
            policy_defaults={"mode": "draft_only"},
        )
    )


def test_signed_beacon_round_trip_and_tamper_detection(client: TestClient) -> None:
    register_package(client)
    response = client.get("/v1/capability/beacons/outreach@v1")
    assert response.status_code == 200
    beacon = response.json()
    assert response.headers["cache-control"] == "private, no-store"
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
    assert keys.headers["cache-control"].startswith("public, max-age=")
    assert keys.json()["keys"][0]["kid"] == beacon["kid"]
    assert keys.json()["keys"][0]["public_key"] == beacon["issuer_public_key"]


def test_beacon_rotation_signs_with_the_key_named_by_kid(
    client: TestClient,
    settings,
) -> None:
    register_package(client, "rotated@v1")
    settings.ei_signing_key = "legacy-ei-signing-seed"
    settings.capability_beacon_keys_json = (
        '{"old":"old-beacon-signing-seed","new":"new-beacon-signing-seed"}'
    )
    settings.capability_beacon_kid = "new"

    response = client.get("/v1/capability/beacons/rotated@v1")
    assert response.status_code == 200
    beacon = response.json()
    assert beacon["kid"] == "new"

    keys = client.get("/.well-known/capability-beacon-keys").json()["keys"]
    new_key = next(item for item in keys if item["kid"] == "new")
    assert beacon["issuer_public_key"] == new_key["public_key"]

    verified = client.post("/v1/capability/beacons/verify", json={"beacon": beacon})
    assert verified.json() == {
        "valid": True,
        "reason": "verified",
        "verified_kid": "new",
    }


def test_expired_beacon_rejected(client: TestClient) -> None:
    register_package(client, "expired@v1")
    beacon = client.get("/v1/capability/beacons/expired@v1").json()
    beacon["expires_at"] = "2020-01-01T00:00:00+00:00"
    rejected = client.post("/v1/capability/beacons/verify", json={"beacon": beacon})
    assert rejected.json() == {
        "valid": False,
        "reason": "beacon_expired",
        "verified_kid": None,
    }


def test_unknown_beacon_kid_is_not_reported_as_verified(client: TestClient) -> None:
    register_package(client, "unknown-kid@v1")
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
    assert response.headers["cache-control"].startswith("public, max-age=")
