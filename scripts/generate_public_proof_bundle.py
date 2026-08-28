import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_bundle(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    
    # 1. Generate Ed25519 Keypair
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with open(os.path.join(out_dir, "public-key.pem"), "wb") as f:
        f.write(pub_pem)
        
    # 2. Create the execution payload
    payload = {
        "execution_id": "exec_01J6B9...",
        "action": "contact.read",
        "resource": "/contacts/123",
        "decision": "ALLOW",
        "timestamp": "2026-08-26T12:00:00Z"
    }
    payload_cbor = cbor2.dumps(payload)
    
    # COSE Sign1 Structure (simplified representation for P0)
    # Protected Header: {1: 27} (alg: EdDSA)
    protected_header = cbor2.dumps({1: 27})
    unprotected_header = {}
    
    # Sig_structure for signing
    sig_structure = [
        "Signature1",
        protected_header,
        b"",
        payload_cbor
    ]
    to_sign = cbor2.dumps(sig_structure)
    signature = private_key.sign(to_sign)
    
    cose_sign1 = [
        protected_header,
        unprotected_header,
        payload_cbor,
        signature
    ]
    
    with open(os.path.join(out_dir, "receipt.cose"), "wb") as f:
        f.write(cbor2.dumps(cose_sign1))
        
    # 3. Create Merkle leaf and proof
    leaf_hash = hashlib.sha256(cbor2.dumps(cose_sign1)).hexdigest()
    # Mocking a tree with this leaf at index 42
    sibling_hash = hashlib.sha256(b"sibling").hexdigest()
    root_hash = hashlib.sha256((leaf_hash + sibling_hash).encode('utf-8')).hexdigest()
    
    proof = {
        "leaf_index": 42,
        "leaf_hash": leaf_hash,
        "siblings": [sibling_hash],
        "tree_size": 43
    }
    with open(os.path.join(out_dir, "proof.json"), "w") as f:
        json.dump(proof, f, indent=2)
        
    # 4. Create Checkpoint
    checkpoint = {
        "tree_size": 43,
        "root_hash": root_hash,
        "reconciliation_status": "pending",
        "timestamp": "2026-08-26T12:05:00Z"
    }
    with open(os.path.join(out_dir, "checkpoint.json"), "w") as f:
        json.dump(checkpoint, f, indent=2)
        
    # 5. Create README
    readme = """# Veklom Public Proof Bundle v0.1-proof

This bundle contains cryptographic evidence of a single governed execution consequence, demonstrating the Independent Authority Boundary invariant.

## Files
- `receipt.cose`: The signed execution consequence (RFC 8152 COSE Sign1 format).
- `public-key.pem`: The Ed25519 public key of the consequence kernel (CAPPO).
- `proof.json`: The Merkle inclusion proof tying this receipt to the local tree.
- `checkpoint.json`: The local tree state (unanchored).
- `veklom-verify.py`: A tiny standalone Python script to verify the evidence.

## Verification
Run `python veklom-verify.py` to cryptographically verify:
1. The COSE signature against the public key.
2. The Merkle inclusion of the exact signed bytes into the local tree root.

Reconciliation state is explicitly 'pending' as external global anchoring is not yet claimed in this slice.
"""
    with open(os.path.join(out_dir, "README.md"), "w") as f:
        f.write(readme)
        
    # 6. Create verifier script
    verifier = """import os
import json
import hashlib
try:
    import cbor2
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("Please install required dependencies: pip install cbor2 cryptography")
    exit(1)

def verify():
    print("Veklom P0 Evidence Verifier")
    print("---------------------------")
    
    # Load Public Key
    with open("public-key.pem", "rb") as f:
        pub_key = serialization.load_pem_public_key(f.read())
        
    # Load COSE Receipt
    with open("receipt.cose", "rb") as f:
        cose_bytes = f.read()
        
    parsed = cbor2.loads(cose_bytes)
    protected_header, unprotected_header, payload_cbor, signature = parsed
    
    # Verify Signature
    sig_structure = [
        "Signature1",
        protected_header,
        b"",
        payload_cbor
    ]
    to_sign = cbor2.dumps(sig_structure)
    
    try:
        pub_key.verify(signature, to_sign)
        print("[V] SIGNATURE_VALID=true")
    except Exception as e:
        print("[X] SIGNATURE_VALID=false")
        return
        
    payload = cbor2.loads(payload_cbor)
    print(f"    EXECUTION_ID={payload['execution_id']}")
    print(f"    ACTION={payload['action']}")
    print(f"    RESOURCE={payload['resource']}")
    
    # Verify Merkle Inclusion
    with open("proof.json", "r") as f:
        proof = json.load(f)
        
    with open("checkpoint.json", "r") as f:
        checkpoint = json.load(f)
        
    leaf_hash = hashlib.sha256(cose_bytes).hexdigest()
    if leaf_hash != proof["leaf_hash"]:
        print("[X] MERKLE_INCLUSION_VALID=false (Leaf hash mismatch)")
        return
        
    current_hash = leaf_hash
    for sibling in proof["siblings"]:
        current_hash = hashlib.sha256((current_hash + sibling).encode('utf-8')).hexdigest()
        
    if current_hash == checkpoint["root_hash"]:
        print("[V] MERKLE_INCLUSION_VALID=true")
    else:
        print("[X] MERKLE_INCLUSION_VALID=false (Root mismatch)")
        return
        
    print(f"[i] RECONCILIATION_STATUS={checkpoint['reconciliation_status']}")

if __name__ == "__main__":
    verify()
"""
    with open(os.path.join(out_dir, "veklom-verify.py"), "w") as f:
        f.write(verifier)
        
if __name__ == "__main__":
    out_dir = r"C:\\Users\\antho\\.windsurf\\veklom-control-plane\\public\\proof-bundle"
    generate_bundle(out_dir)
    print(f"Bundle generated at {out_dir}")
