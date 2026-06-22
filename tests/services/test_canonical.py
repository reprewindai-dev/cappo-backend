import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

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
    # Ensure keys are sorted and no spaces around separators
    assert result == '{"a":1,"b":2,"c":3}'

def test_canonical_json_unicode():
    payload = {"greeting": "你好", "emoji": "🚀"}
    result = canonical_json(payload)
    # Ensure unicode characters are not escaped to ascii
    assert "你好" in result
    assert "🚀" in result

def test_canonical_json_invalid_payload():
    with pytest.raises(ValueError, match="Payload must be a dictionary"):
        canonical_json(["this", "is", "a", "list"])

    with pytest.raises(ValueError, match="Payload must be a dictionary"):
        canonical_json("this is a string")

def test_sha256_json():
    payload = {"test": "data"}
    # Known sha256 of '{"test":"data"}'
    # echo -n '{"test":"data"}' | sha256sum
    result = sha256_json(payload)
    assert result == "e1d7c49f3a04e1ec1a5b150ec68041c903cd75fda52aa1239fd586439ef1154b"

def test_get_ed25519_private_key():
    seed = "test-seed"
    key1 = get_ed25519_private_key(seed)
    key2 = get_ed25519_private_key(seed)

    assert isinstance(key1, ed25519.Ed25519PrivateKey)
    # Private keys derived from the same seed should match
    assert key1.private_bytes_raw() == key2.private_bytes_raw()

def test_sign_verify_ed25519_with_seed():
    payload = {"test": "data"}
    seed = "test-seed"

    signature = sign_payload_ed25519(payload, seed)

    # Verify with the same seed
    assert verify_signature_ed25519(payload, signature, seed) is True

    # Tampering with payload
    assert verify_signature_ed25519({"test": "tampered"}, signature, seed) is False

    # Tampering with signature
    assert verify_signature_ed25519(payload, signature + "x", seed) is False

def test_sign_verify_ed25519_with_keys():
    payload = {"test": "data"}
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    signature = sign_payload_ed25519(payload, private_key)

    # Verify with public key object
    assert verify_signature_ed25519(payload, signature, public_key) is True

    # Verify with public key bytes
    assert verify_signature_ed25519(payload, signature, public_key.public_bytes_raw()) is True

def test_sign_verify_hmac():
    payload = {"test": "data"}
    hmac_key = "test-hmac-key"

    signature = sign_payload_hmac(payload, hmac_key)

    # Verify with the correct key
    assert verify_signature_hmac(payload, signature, hmac_key) is True

    # Tampering with payload
    assert verify_signature_hmac({"test": "tampered"}, signature, hmac_key) is False

    # Tampering with signature
    assert verify_signature_hmac(payload, signature + "x", hmac_key) is False

def test_sign_verify_legacy():
    payload = {"test": "data"}
    signing_key = "legacy-key"

    signature = sign_payload(payload, signing_key)

    # Verify with the correct key
    assert verify_signature(payload, signature, signing_key) is True

    # Tampering with payload
    assert verify_signature({"test": "tampered"}, signature, signing_key) is False

    # Tampering with signature
    assert verify_signature(payload, signature + "f", signing_key) is False

def test_verify_signature_fallback_to_hmac():
    payload = {"test": "data"}
    key = "test-key"

    # Generate an HMAC signature
    hmac_signature = sign_payload_hmac(payload, key)

    # verify_signature_ed25519 should fallback to verify_signature_hmac and return True
    assert verify_signature_ed25519(payload, hmac_signature, key) is True

def test_verify_signature_fallback_to_legacy():
    payload = {"test": "data"}
    key = "test-key"

    # Generate a legacy HMAC signature
    legacy_signature = sign_payload(payload, key)

    # verify_signature_ed25519 should fallback to verify_signature and return True
    assert verify_signature_ed25519(payload, legacy_signature, key) is True

    # verify_signature should also handle it directly
    assert verify_signature(payload, legacy_signature, key) is True

def test_verify_signature_legacy_fallback_to_hmac():
    payload = {"test": "data"}
    key = "test-key"

    # Generate an HMAC signature
    hmac_signature = sign_payload_hmac(payload, key)

    # verify_signature should fallback to verify_signature_hmac and return True
    assert verify_signature(payload, hmac_signature, key) is True
