import base64

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from cappo_backend.services.canonical import (
    canonical_json,
    get_ed25519_private_key,
    sha256_json,
    sign_payload,
    sign_payload_ed25519,
    sign_payload_hmac,
    verify_signature,
    verify_signature_ed25519,
    verify_signature_hmac,
)


def test_canonical_json_sorting_and_separators():
    payload = {"c": 3, "a": 1, "b": 2}
    result = canonical_json(payload)
    assert result == '{"a":1,"b":2,"c":3}'


def test_canonical_json_invalid_payload():
    with pytest.raises(ValueError, match="Payload must be a dictionary"):
        canonical_json(["this", "is", "a", "list"])

    with pytest.raises(ValueError, match="Payload must be a dictionary"):
        canonical_json("this is a string")


def test_sha256_json():
    payload = {"test": "data"}
    result = sha256_json(payload)
    assert result == "e1d7c49f3a04e1ec1a5b150ec68041c903cd75fda52aa1239fd586439ef1154b"


def test_get_ed25519_private_key():
    seed = "test-seed"
    key1 = get_ed25519_private_key(seed)
    key2 = get_ed25519_private_key(seed)

    assert isinstance(key1, ed25519.Ed25519PrivateKey)
    assert key1.private_bytes_raw() == key2.private_bytes_raw()


def test_sign_verify_ed25519_with_seed():
    payload = {"test": "data"}
    seed = "test-seed"
    signature = sign_payload_ed25519(payload, seed)

    assert verify_signature_ed25519(payload, signature, seed) is True
    assert verify_signature_ed25519({"test": "tampered"}, signature, seed) is False
    assert verify_signature_ed25519(payload, signature + "x", seed) is False
    assert verify_signature_ed25519(payload, signature[:10] + "***" + signature[10:], seed) is False


def test_sign_verify_ed25519_with_keys():
    payload = {"test": "data"}
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    signature = sign_payload_ed25519(payload, private_key)

    assert verify_signature_ed25519(payload, signature, public_key) is True
    assert verify_signature_ed25519(payload, signature, public_key.public_bytes_raw()) is True


def test_sign_verify_ed25519_with_capi_spki_base64_key():
    payload = {"test": "data"}
    private_key = ed25519.Ed25519PrivateKey.generate()
    signature = sign_payload_ed25519(payload, private_key)
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")

    assert verify_signature_ed25519(payload, signature, public_key_b64) is True


def test_sign_verify_hmac():
    payload = {"test": "data"}
    hmac_key = "test-hmac-key"
    signature = sign_payload_hmac(payload, hmac_key)

    assert verify_signature_hmac(payload, signature, hmac_key) is True
    assert verify_signature_hmac({"test": "tampered"}, signature, hmac_key) is False
    assert verify_signature_hmac(payload, signature + "x", hmac_key) is False


def test_sign_verify_legacy():
    payload = {"test": "data"}
    signing_key = "legacy-key"
    signature = sign_payload(payload, signing_key)

    assert verify_signature(payload, signature, signing_key) is True
    assert verify_signature({"test": "tampered"}, signature, signing_key) is False
    assert verify_signature(payload, signature + "f", signing_key) is False


def test_ed25519_verifier_rejects_hmac_signature():
    payload = {"test": "data"}
    key = "test-key"
    hmac_signature = sign_payload_hmac(payload, key)

    assert verify_signature_ed25519(payload, hmac_signature, key) is False
    assert verify_signature_hmac(payload, hmac_signature, key) is True


def test_ed25519_verifier_rejects_legacy_signature():
    payload = {"test": "data"}
    key = "test-key"
    legacy_signature = sign_payload(payload, key)

    assert verify_signature_ed25519(payload, legacy_signature, key) is False
    assert verify_signature(payload, legacy_signature, key) is True


def test_verify_signature_legacy_fallback_to_hmac():
    payload = {"test": "data"}
    key = "test-key"
    hmac_signature = sign_payload_hmac(payload, key)

    assert verify_signature(payload, hmac_signature, key) is True
