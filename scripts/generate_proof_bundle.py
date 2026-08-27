#!/usr/bin/env python3
"""
generate_proof_bundle.py
========================
Generates a self-consistent, cryptographically correct P0 proof bundle for
the Veklom /proof page.

Protocol version: v1

Design constraints (all required for veklom-verify.py to accept the bundle):
  - COSE_Sign1 encoding: CBOR Tag 18, algorithm header {1: -8} (EdDSA/Ed25519)
  - Sig_Structure: ["Signature1", protected_header_bytes, b"", payload_cbor]
  - key_id (kid): "veklom-evidence-key-v1" in unprotected header (label 4)
  - Merkle leaf:    SHA-256(0x00 || cose_bytes)             (RFC 6962 §2.1)
  - Merkle parent:  SHA-256(0x01 || left_bytes || right_bytes)  (RFC 6962 §2.1)
  - Inclusion proof: full RFC-6962-compatible siblings list for a REAL tree
  - Checkpoint:     {"tree_size", "root_hash_hex", "timestamp",
                     "key_id", "checkpoint_sig_hex"} — checkpoint_sig is an
                     Ed25519 signature over the canonical checkpoint payload
                     bytes (CBOR-encoded deterministic map), so the verifier
                     can cryptographically bind the root to the key.

Usage:
    cd cappo-backend
    python scripts/generate_proof_bundle.py --out ../veklom-control-plane/public/proof-bundle
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import base64
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap path so we can import cappo_backend without installing it
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import cbor2
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError as e:
    sys.exit(f"Missing dependency: {e}\n  pip install cbor2 cryptography")

from cappo_backend.security.evidence import (
    get_evidence_key_pair,
    mint_signed_execution_evidence,
)
from cappo_backend.security.merkle import (
    AppendOnlyMerkleTree,
    hash_leaf,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KEY_ID = b"veklom-evidence-key-v1"

# ---------------------------------------------------------------------------
# Canonical receipt payload — a real-looking (but synthetic demo) receipt
# All fields are representative; adapt for a real production run.
# ---------------------------------------------------------------------------
CANONICAL_RECEIPT: dict = {
    "schema_version": 1,
    "execution_id": "exec_DEMO_G1_1_AI1",
    "action": "db:write",
    "resource": "postgresql://cappo@db/cappo.capability_action_receipts",
    "capability_scope": ["db:write:capability_action_receipts"],
    "consequence_allowed": True,
    "evidence_required": True,
    "commit_sha": "4c0a59f",                        # adversarial suite commit
    "substrate": "local-pytest / cappo-backend",
    "test_ids": [
        "test_ai_1_authority_ingress",
        "test_am_1_authority_monotonicity",
        "test_cd_1_consequence_domination",
        "test_zra_1_terminal_replay",
        "test_ec_1_evidence_gap",
        "test_scitt_interoperability",
    ],
}

# ---------------------------------------------------------------------------
# Helper: build the checkpoint CBOR payload that gets signed
# ---------------------------------------------------------------------------
def _checkpoint_payload(tree_size: int, root_hex: str, ts: str, key_id: bytes) -> bytes:
    data = {
        "tree_size": tree_size,
        "root_hash_hex": root_hex,
        "timestamp": ts,
        "key_id": key_id.decode(),
    }
    return cbor2.dumps(data, canonical=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate Veklom P0 proof bundle")
    ap.add_argument("--out", default="../veklom-control-plane/public/proof-bundle",
                    help="Output directory for bundle files")
    ap.add_argument("--leaves", type=int, default=43,
                    help="Total number of leaves in the synthetic demo tree (must be ≥ 2)")
    ap.add_argument("--target-leaf", type=int, default=7,
                    help="Index of the leaf whose inclusion is being proven (0-based)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    n_leaves: int = args.leaves
    target_idx: int = args.target_leaf

    if not (0 <= target_idx < n_leaves):
        sys.exit(f"--target-leaf {target_idx} out of range for --leaves {n_leaves}")

    # -----------------------------------------------------------------------
    # 1. Load / generate the Ed25519 evidence key
    # -----------------------------------------------------------------------
    private_key: ed25519.Ed25519PrivateKey = get_evidence_key_pair()
    public_key: ed25519.Ed25519PublicKey = private_key.public_key()

    # -----------------------------------------------------------------------
    # 2. Mint a real COSE_Sign1 receipt for the canonical receipt payload
    # -----------------------------------------------------------------------
    cose_bytes: bytes = mint_signed_execution_evidence(
        canonical_receipt=CANONICAL_RECEIPT,
        key_id=KEY_ID,
    )

    # -----------------------------------------------------------------------
    # 3. Build a synthetic demo Merkle tree.
    #    Every leaf except target_idx gets a deterministic filler value;
    #    target_idx is the real COSE receipt.
    # -----------------------------------------------------------------------
    tree = AppendOnlyMerkleTree()
    for i in range(n_leaves):
        if i == target_idx:
            tree.append(cose_bytes)
        else:
            filler = hashlib.sha256(f"filler-leaf-{i}".encode()).digest()
            tree.append(filler)

    root_bytes: bytes = tree.root()
    root_hex: str = root_bytes.hex()

    inclusion_siblings: list[bytes] = tree.inclusion_proof(target_idx)
    siblings_hex: list[str] = [s.hex() for s in inclusion_siblings]

    # The leaf hash is SHA-256(0x00 || cose_bytes) — RFC 6962 domain separated
    leaf_hash_bytes: bytes = hash_leaf(cose_bytes)
    leaf_hash_hex: str = leaf_hash_bytes.hex()

    # -----------------------------------------------------------------------
    # 4. Sign the checkpoint
    # -----------------------------------------------------------------------
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ckpt_payload_bytes = _checkpoint_payload(n_leaves, root_hex, ts, KEY_ID)
    ckpt_sig: bytes = private_key.sign(ckpt_payload_bytes)
    ckpt_sig_hex: str = ckpt_sig.hex()

    # -----------------------------------------------------------------------
    # 5. Export public key as PEM
    # -----------------------------------------------------------------------
    pub_pem: bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # -----------------------------------------------------------------------
    # 6. Write all bundle files
    # -----------------------------------------------------------------------

    (out / "receipt.cose").write_bytes(cose_bytes)
    print(f"[+] receipt.cose  ({len(cose_bytes)} bytes)")

    (out / "public-key.pem").write_bytes(pub_pem)
    print(f"[+] public-key.pem")

    proof_doc = {
        "protocol_version": 1,
        "leaf_index": target_idx,
        "tree_size": n_leaves,
        "leaf_hash_hex": leaf_hash_hex,
        "siblings_hex": siblings_hex,
        "merkle_algorithm": "RFC-6962-SHA256",
    }
    (out / "proof.json").write_text(json.dumps(proof_doc, indent=2))
    print(f"[+] proof.json    (leaf_index={target_idx}, tree_size={n_leaves}, "
          f"{len(siblings_hex)} siblings)")

    checkpoint_doc = {
        "protocol_version": 1,
        "tree_size": n_leaves,
        "root_hash_hex": root_hex,
        "timestamp": ts,
        "key_id": KEY_ID.decode(),
        "checkpoint_sig_hex": ckpt_sig_hex,
        "reconciliation_status": "NOT_YET_CLAIMED",
    }
    (out / "checkpoint.json").write_text(json.dumps(checkpoint_doc, indent=2))
    print(f"[+] checkpoint.json (signed, root={root_hex[:16]}…)")

    # Artifact SHA-256 hashes for independent verification
    receipt_sha = hashlib.sha256(cose_bytes).hexdigest()
    pubkey_sha  = hashlib.sha256(pub_pem).hexdigest()
    proof_bytes = (out / "proof.json").read_bytes()
    proof_sha   = hashlib.sha256(proof_bytes).hexdigest()
    ckpt_bytes  = (out / "checkpoint.json").read_bytes()
    ckpt_sha    = hashlib.sha256(ckpt_bytes).hexdigest()

    print("\nArtifact SHA-256 hashes (embed these on the /proof page):")
    print(f"  receipt.cose:    {receipt_sha}")
    print(f"  public-key.pem:  {pubkey_sha}")
    print(f"  proof.json:      {proof_sha}")
    print(f"  checkpoint.json: {ckpt_sha}")


if __name__ == "__main__":
    main()
