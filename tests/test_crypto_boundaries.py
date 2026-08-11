from __future__ import annotations

from importlib.metadata import version

from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives import serialization

from cappo_backend.services.canonical import (
    get_ed25519_private_key,
    sign_payload_ed25519,
    sign_payload_hmac,
    verify_signature_ed25519,
)
from cappo_backend.services.ei_builder import Ed25519Signer, ExecutionIdentityBuilder, canonical_body


SEED = "crypto-boundary-test-seed"


def _ei_inputs() -> dict[str, object]:
    return {
        "pgl_pre_certificate_id": "pgl_pre_test",
        "genome_hash": "genome-hash",
        "constitution_hash": "constitution-hash",
        "plan_hash": "plan-hash",
        "directive": "ALLOW",
        "risk_tier": "standard",
        "scope": {"tools": ["llm.exec"]},
        "issuer": "cappo-test",
        "execution_id": "ei-crypto-boundary",
    }


def test_cryptography_major_version_and_openssl_runtime_are_supported() -> None:
    major = int(version("cryptography").split(".", 1)[0])
    assert major >= 50
    assert backend.openssl_version_number() >= 0x30000000


def test_ed25519_round_trip_with_seed_and_raw_public_key() -> None:
    payload = {"run_id": "run-1", "decision": "allow"}
    signature = sign_payload_ed25519(payload, SEED)
    private_key = get_ed25519_private_key(SEED)
    raw_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    assert verify_signature_ed25519(payload, signature, SEED) is True
    assert verify_signature_ed25519(payload, signature, raw_public_key) is True


def test_ed25519_rejects_tampered_payload_wrong_key_and_malformed_signature() -> None:
    payload = {"run_id": "run-1", "decision": "allow"}
    signature = sign_payload_ed25519(payload, SEED)

    assert verify_signature_ed25519(
        {"run_id": "run-1", "decision": "deny"}, signature, SEED
    ) is False
    assert verify_signature_ed25519(payload, signature, "wrong-seed") is False
    assert verify_signature_ed25519(payload, "not-base64***", SEED) is False


def test_hmac_signature_cannot_satisfy_ed25519_verifier() -> None:
    payload = {"run_id": "run-1", "decision": "allow"}
    hmac_signature = sign_payload_hmac(payload, SEED)

    assert verify_signature_ed25519(payload, hmac_signature, SEED) is False


def test_execution_identity_signature_fails_after_body_tamper() -> None:
    signer = Ed25519Signer(SEED)
    identity = ExecutionIdentityBuilder(signer=signer).build(_ei_inputs())
    assert signer.verify(canonical_body(identity), identity["signature"]) is True

    tampered = dict(identity)
    tampered["directive"] = "DENY"
    assert signer.verify(canonical_body(tampered), identity["signature"]) is False
