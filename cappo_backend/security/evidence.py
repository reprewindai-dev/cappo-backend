import os
import cbor2
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature

def get_evidence_key_pair() -> ed25519.Ed25519PrivateKey:
    # check env
    env_hex = os.environ.get('EVIDENCE_ROOT_PRIVATE_KEY_HEX')
    if env_hex:
        try:
            return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(env_hex))
        except Exception:
            pass
    # fallback to local file
    key_file = '.evidence_root_key'
    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            return ed25519.Ed25519PrivateKey.from_private_bytes(f.read(32))
    else:
        pk = ed25519.Ed25519PrivateKey.generate()
        try:
            from cryptography.hazmat.primitives import serialization
            raw = pk.private_bytes(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw, encryption_algorithm=serialization.NoEncryption())
            with open(key_file, 'wb') as f:
                f.write(raw)
        except Exception:
            pass
        return pk

def mint_signed_execution_evidence(
    canonical_receipt: dict, 
    private_key: ed25519.Ed25519PrivateKey, 
    key_id: bytes = b"veklom-evidence-key"
) -> bytes:
    payload = cbor2.dumps(canonical_receipt, canonical=True)
    protected_header = cbor2.dumps({1: -8}, canonical=True)
    unprotected_header = {4: key_id}
    sig_structure = ["Signature1", protected_header, b"", payload]
    sig_data = cbor2.dumps(sig_structure, canonical=True)
    signature = private_key.sign(sig_data)
    cose_sign1 = [protected_header, unprotected_header, payload, signature]
    return cbor2.dumps(cbor2.CBORTag(18, cose_sign1), canonical=True)

def verify_signed_execution_evidence(
    cose_bytes: bytes, 
    public_key: ed25519.Ed25519PublicKey
) -> dict:
    try:
        decoded_tag = cbor2.loads(cose_bytes)
    except Exception as e:
        raise ValueError(f"Failed to parse COSE bytes: {e}")
    if not isinstance(decoded_tag, cbor2.CBORTag) or decoded_tag.tag != 18:
        raise ValueError("Not a valid COSE_Sign1 tagged object")
    try:
        protected_header, unprotected_header, payload, signature = decoded_tag.value
    except ValueError:
        raise ValueError("COSE_Sign1 array must have exactly 4 elements")
    sig_struct = ["Signature1", protected_header, b"", payload]
    sig_data = cbor2.dumps(sig_struct, canonical=True)
    try:
        public_key.verify(signature, sig_data)
    except InvalidSignature:
        raise ValueError("Signature verification failed")
    try:
        receipt = cbor2.loads(payload)
    except Exception as e:
        raise ValueError(f"Failed to parse payload as CBOR: {e}")
    return receipt

