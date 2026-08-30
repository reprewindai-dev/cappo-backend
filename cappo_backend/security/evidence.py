import os

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


EVIDENCE_KEY_ID = b"veklom-evidence-key-v1"
COSE_ALG_EDDSA = -8


def get_evidence_key_pair() -> ed25519.Ed25519PrivateKey:
    """Load the configured evidence signing key, or the persisted local key."""
    env_hex = os.environ.get("EVIDENCE_ROOT_PRIVATE_KEY_HEX")
    if env_hex:
        try:
            return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(env_hex))
        except Exception:
            pass

    key_file = ".evidence_root_key"
    if os.path.exists(key_file):
        with open(key_file, "rb") as handle:
            return ed25519.Ed25519PrivateKey.from_private_bytes(handle.read(32))

    private_key = ed25519.Ed25519PrivateKey.generate()
    try:
        raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with open(key_file, "wb") as handle:
            handle.write(raw)
    except Exception:
        pass
    return private_key


def get_evidence_public_key() -> ed25519.Ed25519PublicKey:
    """Return the verification key corresponding to the active evidence root."""
    return get_evidence_key_pair().public_key()


def mint_signed_execution_evidence(
    canonical_receipt: dict,
    key_file: str = ".evidence_root_key",
    key_id: bytes = EVIDENCE_KEY_ID,
    private_key: ed25519.Ed25519PrivateKey | None = None,
) -> bytes:
    del key_file  # retained for backwards-compatible call signatures
    payload = cbor2.dumps(canonical_receipt, canonical=True)
    protected_header = cbor2.dumps({1: COSE_ALG_EDDSA}, canonical=True)
    unprotected_header = {4: key_id}
    sig_structure = ["Signature1", protected_header, b"", payload]
    sig_data = cbor2.dumps(sig_structure, canonical=True)

    if private_key is None:
        private_key = get_evidence_key_pair()

    signature = private_key.sign(sig_data)
    cose_sign1 = [protected_header, unprotected_header, payload, signature]
    return cbor2.dumps(cbor2.CBORTag(18, cose_sign1), canonical=True)


def verify_signed_execution_evidence(
    cose_bytes: bytes,
    public_key: ed25519.Ed25519PublicKey,
    expected_key_id: bytes | None = EVIDENCE_KEY_ID,
) -> dict:
    """Verify and decode a CAPPO COSE_Sign1 authorization receipt.

    Verification is fail-closed: the object must be a tagged COSE_Sign1,
    declare EdDSA in the protected header, use the expected evidence key id,
    carry byte-string payload/signature fields, and pass Ed25519 verification.
    """
    try:
        decoded_tag = cbor2.loads(cose_bytes)
    except Exception as exc:
        raise ValueError(f"Failed to parse COSE bytes: {exc}") from exc

    if not isinstance(decoded_tag, cbor2.CBORTag) or decoded_tag.tag != 18:
        raise ValueError("Not a valid COSE_Sign1 tagged object")
    if not isinstance(decoded_tag.value, list) or len(decoded_tag.value) != 4:
        raise ValueError("COSE_Sign1 array must have exactly 4 elements")

    protected_header, unprotected_header, payload, signature = decoded_tag.value
    if not isinstance(protected_header, bytes):
        raise ValueError("COSE protected header must be a byte string")
    if not isinstance(unprotected_header, dict):
        raise ValueError("COSE unprotected header must be a map")
    if not isinstance(payload, bytes):
        raise ValueError("COSE payload must be a byte string")
    if not isinstance(signature, bytes):
        raise ValueError("COSE signature must be a byte string")

    try:
        protected = cbor2.loads(protected_header)
    except Exception as exc:
        raise ValueError(f"Failed to parse COSE protected header: {exc}") from exc
    if not isinstance(protected, dict) or protected.get(1) != COSE_ALG_EDDSA:
        raise ValueError("COSE protected header must declare EdDSA")
    if expected_key_id is not None and unprotected_header.get(4) != expected_key_id:
        raise ValueError("COSE evidence key id mismatch")

    sig_struct = ["Signature1", protected_header, b"", payload]
    sig_data = cbor2.dumps(sig_struct, canonical=True)
    try:
        public_key.verify(signature, sig_data)
    except InvalidSignature as exc:
        raise ValueError("Signature verification failed") from exc

    try:
        receipt = cbor2.loads(payload)
    except Exception as exc:
        raise ValueError(f"Failed to parse payload as CBOR: {exc}") from exc
    if not isinstance(receipt, dict):
        raise ValueError("Signed execution evidence payload must be a map")
    return receipt
