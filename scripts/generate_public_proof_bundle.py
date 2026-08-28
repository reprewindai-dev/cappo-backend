import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_bundle(out_dir):
    """Generate a synthetic proof fixture for verifier-structure testing only."""
    os.makedirs(out_dir, exist_ok=True)

    # 1. Generate an ephemeral fixture keypair.
    # This is not CAPPO production signer material and carries no production provenance.
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with open(os.path.join(out_dir, "public-key.pem"), "wb") as f:
        f.write(pub_pem)

    # 2. Create an unmistakably synthetic execution payload.
    payload = {
        "execution_id": "fixture-execution-001",
        "action": "fixture.read",
        "resource": "/fixture/resource/001",
        "decision": "ALLOW",
        "timestamp": "2026-08-26T12:00:00Z",
    }
    payload_cbor = cbor2.dumps(payload)

    # COSE Sign1 Structure (simplified representation for fixture testing).
    # Protected Header: {1: 27} (alg: EdDSA)
    protected_header = cbor2.dumps({1: 27})
    unprotected_header = {}

    sig_structure = [
        "Signature1",
        protected_header,
        b"",
        payload_cbor,
    ]
    to_sign = cbor2.dumps(sig_structure)
    signature = private_key.sign(to_sign)

    cose_sign1 = [
        protected_header,
        unprotected_header,
        payload_cbor,
        signature,
    ]

    with open(os.path.join(out_dir, "receipt.cose"), "wb") as f:
        f.write(cbor2.dumps(cose_sign1))

    # 3. Create a mocked local Merkle fixture.
    # This does not represent a Gnomledger tree or durable production commitment.
    leaf_hash = hashlib.sha256(cbor2.dumps(cose_sign1)).hexdigest()
    sibling_hash = hashlib.sha256(b"synthetic-fixture-sibling").hexdigest()
    root_hash = hashlib.sha256((leaf_hash + sibling_hash).encode("utf-8")).hexdigest()

    proof = {
        "fixture": True,
        "leaf_index": 42,
        "leaf_hash": leaf_hash,
        "siblings": [sibling_hash],
        "tree_size": 43,
    }
    with open(os.path.join(out_dir, "proof.json"), "w", encoding="utf-8") as f:
        json.dump(proof, f, indent=2)

    # 4. Create an explicitly unanchored fixture checkpoint.
    checkpoint = {
        "fixture": True,
        "tree_size": 43,
        "root_hash": root_hash,
        "reconciliation_status": "pending",
        "timestamp": "2026-08-26T12:05:00Z",
    }
    with open(os.path.join(out_dir, "checkpoint.json"), "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2)

    # 5. Create fixture README.
    readme = """# Veklom Synthetic Proof Fixture v0.1

Status: EXPERIMENTAL_STRUCTURE_VALIDATION / SYNTHETIC_FIXTURE

This bundle is generated locally from an ephemeral keypair, a synthetic execution payload,
a mocked Merkle sibling/tree, and an unanchored checkpoint. It is intended only to exercise
the standalone verifier structure.

It does NOT prove a live governed execution, CAPPO production-signer provenance,
Lockerphycer consequence execution, durable Gnomledger commitment, reconciliation,
or an on-chain/global anchor.

## Files
- `receipt.cose`: synthetic COSE Sign1 fixture bytes.
- `public-key.pem`: ephemeral fixture public key generated for this bundle.
- `proof.json`: mocked local Merkle fixture.
- `checkpoint.json`: unanchored fixture checkpoint.
- `veklom-verify.py`: standalone structural verifier for these fixture bytes.

## Verification
Run `python veklom-verify.py` to check:
1. Internal Ed25519 signature consistency for the generated fixture.
2. Internal Merkle-root consistency for the mocked fixture tree.

Passing these checks remains EXPERIMENTAL_STRUCTURE_VALIDATION. It must not be reported
as durable or production execution verification.
"""
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)

    # 6. Create verifier script.
    verifier = """import json
import hashlib
try:
    import cbor2
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("Please install required dependencies: pip install cbor2 cryptography")
    raise SystemExit(1)


def verify():
    print("Veklom Synthetic Proof Fixture Verifier")
    print("Status: EXPERIMENTAL_STRUCTURE_VALIDATION")
    print("-----------------------------------------")

    with open("public-key.pem", "rb") as f:
        pub_key = serialization.load_pem_public_key(f.read())

    with open("receipt.cose", "rb") as f:
        cose_bytes = f.read()

    parsed = cbor2.loads(cose_bytes)
    protected_header, unprotected_header, payload_cbor, signature = parsed

    sig_structure = [
        "Signature1",
        protected_header,
        b"",
        payload_cbor,
    ]
    to_sign = cbor2.dumps(sig_structure)

    try:
        pub_key.verify(signature, to_sign)
        print("[V] FIXTURE_SIGNATURE_VALID=true")
    except Exception:
        print("[X] FIXTURE_SIGNATURE_VALID=false")
        return

    payload = cbor2.loads(payload_cbor)
    print(f"    FIXTURE_EXECUTION_ID={payload['execution_id']}")
    print(f"    FIXTURE_ACTION={payload['action']}")
    print(f"    FIXTURE_RESOURCE={payload['resource']}")

    with open("proof.json", "r", encoding="utf-8") as f:
        proof = json.load(f)

    with open("checkpoint.json", "r", encoding="utf-8") as f:
        checkpoint = json.load(f)

    if proof.get("fixture") is not True or checkpoint.get("fixture") is not True:
        print("[X] FIXTURE_MARKER_MISSING=true")
        return

    leaf_hash = hashlib.sha256(cose_bytes).hexdigest()
    if leaf_hash != proof["leaf_hash"]:
        print("[X] FIXTURE_MERKLE_VALID=false (leaf hash mismatch)")
        return

    current_hash = leaf_hash
    for sibling in proof["siblings"]:
        current_hash = hashlib.sha256((current_hash + sibling).encode("utf-8")).hexdigest()

    if current_hash == checkpoint["root_hash"]:
        print("[V] FIXTURE_MERKLE_VALID=true")
    else:
        print("[X] FIXTURE_MERKLE_VALID=false (root mismatch)")
        return

    print(f"[i] RECONCILIATION_STATUS={checkpoint['reconciliation_status']}")
    print("[i] EVIDENCE_CLASS=SYNTHETIC_FIXTURE")


if __name__ == "__main__":
    verify()
"""
    with open(os.path.join(out_dir, "veklom-verify.py"), "w", encoding="utf-8") as f:
        f.write(verifier)


if __name__ == "__main__":
    default_out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "artifacts",
        "proof-bundle-fixture",
    )
    out_dir = os.environ.get("VEKLOM_PROOF_BUNDLE_OUT", default_out)
    generate_bundle(out_dir)
    print(f"Synthetic proof fixture generated at {out_dir}")
