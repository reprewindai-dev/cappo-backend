"""Shared contract vector: CAPPO must reproduce every value byte for byte.

The same JSON lives in demo-arena/target/tests/. If either side changes how it builds
the permit MAC or the signed redemption message (ordering, newlines, encoding, domain
markers), one of the two repos fails here before the services disagree in production.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cappo_backend.capability_mount.permits import (
    mint_permit,
    payload_digest,
    redeem_message,
    target_signature_valid,
)

V = json.loads((Path(__file__).parents[1] / "fixtures" / "redeem_contract_vector_v1_TEST_ONLY_KEYS.json").read_text())


def _permit(target_ref: str = V["target_ref"], payload_sha256: str = V["payload_sha256"]) -> str:
    return mint_permit(bytes.fromhex(V["permit_key_hex"]), operation_id=V["operation_id"], mount_id=V["mount_id"],
                       receipt_id=V["receipt_id"], payload_sha256=payload_sha256, target_ref=target_ref)


def _message(target_ref: str = V["target_ref"]) -> bytes:
    return redeem_message(operation_id=V["operation_id"], payload_sha256=V["payload_sha256"],
                          permit=V["expected_permit_hex"], target_ref=target_ref)


def test_payload_digest_is_over_the_exact_bytes():
    assert payload_digest(V["payload_utf8"].encode()) == V["payload_sha256"]


def test_permit_v2_reproduces_byte_for_byte():
    assert _permit() == V["expected_permit_hex"]


def test_permit_changes_with_the_target():
    assert _permit(target_ref=V["wrong_target_ref"]) != V["expected_permit_hex"]


def test_redeem_message_reproduces_byte_for_byte():
    assert _message().hex() == V["expected_redeem_message_hex"]


def test_the_targets_signature_verifies_against_the_pinned_key():
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(V["target_private_seed_hex"]))
    assert private.sign(_message()).hex() == V["expected_signature_hex"]
    assert target_signature_valid(V["target_public_key_hex"], V["expected_signature_hex"], _message())


def test_signature_does_not_transfer_to_another_target_or_payload():
    sig = V["expected_signature_hex"]
    assert not target_signature_valid(V["target_public_key_hex"], sig, _message(V["wrong_target_ref"]))
    tampered = redeem_message(operation_id=V["operation_id"], payload_sha256="0" * 64,
                              permit=V["expected_permit_hex"], target_ref=V["target_ref"])
    assert not target_signature_valid(V["target_public_key_hex"], sig, tampered)


# Both repos must consume identical fixture bytes. If you change the vector, change it in
# BOTH repos together and update this hash in both tests; never in one alone.
FIXTURE_SHA256 = "89957c502335629d86bdce43d765470cee8156525c95da3793f4c6433f39966a"


def test_fixture_is_the_shared_one_byte_for_byte():
    assert hashlib.sha256((Path(__file__).parents[1] / "fixtures" / "redeem_contract_vector_v1_TEST_ONLY_KEYS.json").read_bytes()).hexdigest() == FIXTURE_SHA256


def test_signature_does_not_transfer_to_another_permit():
    other_permit = "0" * 64
    msg = redeem_message(operation_id=V["operation_id"], payload_sha256=V["payload_sha256"],
                         permit=other_permit, target_ref=V["target_ref"])
    assert msg.hex() != V["expected_redeem_message_hex"]
    assert not target_signature_valid(V["target_public_key_hex"], V["expected_signature_hex"], msg)
