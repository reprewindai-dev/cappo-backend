import cbor2
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from cappo_backend.security.evidence import (
    mint_signed_execution_evidence,
    verify_signed_execution_evidence,
)


def test_g0b5_signed_evidence():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    
    receipt = {
        "execution_id": "exec_12345",
        "caller_spiffe_id": "spiffe://example.org/workload/cappo-backend",
        "executor_spiffe_id": "spiffe://example.org/workload/cappo-backend",
        "caller_cert_sha256": "abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234",
        "capability_id": "echo@v1",
        "biscuit_token_sha256": "eff1234567890abcdef1234567890abcdef1234567890abcdef1234567890a",
        "action": "read",
        "resource": "/records/customer-42",
        "policy_version": "1.0",
        "decision": 0,  # ALLOW
        "reason": "allowed",
        "timestamp": "2026-08-26T14:04:01Z",
        "result_hash": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    }
    
    # Mint COSE_Sign1
    cose_bytes = mint_signed_execution_evidence(receipt, private_key=priv)
    assert cose_bytes is not None
    assert isinstance(cose_bytes, bytes)
    
    # Basic parsing check
    parsed = cbor2.loads(cose_bytes)
    assert parsed.tag == 18
    assert len(parsed.value) == 4
    
    # 1. Verification with correct key
    verified_receipt = verify_signed_execution_evidence(cose_bytes, pub)
    assert verified_receipt == receipt
    
    # 2. Tampering execution_id
    tampered_receipt = receipt.copy()
    tampered_receipt["execution_id"] = "exec_99999"
    cose_tampered_exec_id = _tamper_cose_payload(cose_bytes, tampered_receipt)
    with pytest.raises(ValueError, match="Signature verification failed"):
        verify_signed_execution_evidence(cose_tampered_exec_id, pub)
        
    # 3. Tampering action
    tampered_receipt = receipt.copy()
    tampered_receipt["action"] = "write"
    cose_tampered_action = _tamper_cose_payload(cose_bytes, tampered_receipt)
    with pytest.raises(ValueError, match="Signature verification failed"):
        verify_signed_execution_evidence(cose_tampered_action, pub)
        
    # 4. Tampering result_hash
    tampered_receipt = receipt.copy()
    tampered_receipt["result_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
    cose_tampered_result = _tamper_cose_payload(cose_bytes, tampered_receipt)
    with pytest.raises(ValueError, match="Signature verification failed"):
        verify_signed_execution_evidence(cose_tampered_result, pub)
        
    # 5. Tampering biscuit_token_sha256
    tampered_receipt = receipt.copy()
    tampered_receipt["biscuit_token_sha256"] = "1111111111111111111111111111111111111111111111111111111111111111"
    cose_tampered_biscuit = _tamper_cose_payload(cose_bytes, tampered_receipt)
    with pytest.raises(ValueError, match="Signature verification failed"):
        verify_signed_execution_evidence(cose_tampered_biscuit, pub)
        
    # 6. Wrong public key
    wrong_priv = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="Signature verification failed"):
        verify_signed_execution_evidence(cose_bytes, wrong_priv.public_key())
        
    # 7. Truncated COSE
    with pytest.raises(ValueError):
        verify_signed_execution_evidence(cose_bytes[:-5], pub)


def _tamper_cose_payload(cose_bytes: bytes, new_payload: dict) -> bytes:
    # Helper to reconstruct COSE_Sign1 with a tampered payload but the original signature
    parsed = cbor2.loads(cose_bytes)
    ph, uh, _p, sig = parsed.value
    tampered_cose = cbor2.CBORTag(18, [ph, uh, cbor2.dumps(new_payload, canonical=True), sig])
    return cbor2.dumps(tampered_cose, canonical=True)

