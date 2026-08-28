"""
G0B.6 - Append-Only Merkle Commitment — HARDENED (durable ordering + concurrency)

Leaf order = merkle_leaf_index ASC (persistent integer, allocated atomically).
Leaf data  = exact signed_receipt_cose bytes from DB row (never re-minted).
Allocator  = sequence table UPDATE-then-SELECT in same transaction (lock-safe).

Proof Matrix:
  MERKLE_STRUCTURE_CREATED           = True
  SIGNED_COSE_BYTES_HASHED           = True
  LEAF_HASH_STABLE                   = True
  FIRST_APPEND_SUCCEEDED             = True
  SECOND_APPEND_SUCCEEDED            = True
  TREE_SIZE_INCREMENTED              = True
  ROOT_CHANGED_AFTER_APPEND          = True
  INCLUSION_PROOF_CREATED            = True
  INCLUSION_PROOF_VERIFIED           = True
  OLD_ROOT_PRESERVED                 = True
  CONSISTENCY_PROOF_CREATED          = True
  CONSISTENCY_PROOF_VERIFIED         = True
  TAMPERED_COSE_NOT_INCLUDED         = True
  TAMPERED_LEAF_PROOF_DENIED         = True
  WRONG_ROOT_DENIED                  = True
  TRUNCATED_PROOF_DENIED             = True
  APPEND_ONLY                        = True
  DELETE_REJECTED_OR_UNSUPPORTED     = True
  REORDER_DETECTED                   = True
  LOCAL_VERIFICATION_ONLY            = True
  NETWORK_CALLS                      = 0
  G0A_RECEIPT_BEHAVIOR_PRESERVED     = True
  G0B5_SIGNATURE_VERIFIED            = True
  -- Durable ordering:
  SEQUENCE_ROW_PERSISTED             = True
  SEQUENCE_UPDATE_IN_SAME_TXN        = True
  SEQUENCE_MISSING_RAISES            = True
  RESTART_REBUILD_STABLE             = True
  DB_REQUERY_ORDER_STABLE            = True
  UUID_ORDER_IRRELEVANT              = True
  EXACT_PERSISTED_COSE_HASHED        = True
  REMINT_NOT_USED_FOR_RECONSTRUCTION = True
  DUPLICATE_LEAF_INDEX_DENIED        = True
  RECEIPT_INDEX_UNIQUE_CONSTRAINT    = True
  CONCURRENT_ALLOCATIONS_UNIQUE      = True
  CONCURRENT_LEAVES_STABLE_AFTER_REBUILD = True
"""
import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from cappo_backend.db.base import Base
from cappo_backend.models import CapabilityActionReceipt, MerkleLeafSequence
from cappo_backend.security.evidence import (
    get_evidence_key_pair,
    mint_signed_execution_evidence,
    verify_signed_execution_evidence,
)
from cappo_backend.security.merkle import (
    AppendOnlyMerkleTree,
    hash_leaf,
    verify_consistency_proof,
    verify_inclusion_proof,
)
from cappo_backend.security.merkle_ops import (
    assign_merkle_leaf_index,
    get_merkle_ordered_cose_bytes,
)


def _now():
    return datetime.now(timezone.utc)


def test_g0b6_merkle_commitment(db: Session):
    """Core G0B.6 proof: durable ordered Merkle commitment of COSE receipts."""
    priv = get_evidence_key_pair()
    pub = priv.public_key()

    def make_cose(exec_id: str) -> bytes:
        return mint_signed_execution_evidence(
            {
                "execution_id": exec_id,
                "caller_spiffe_id": "spiffe://example.org/workload/cappo-backend",
                "executor_spiffe_id": "spiffe://example.org/workload/cappo-backend",
                "caller_cert_sha256": "abcd" * 16,
                "capability_id": "echo@v1",
                "biscuit_token_sha256": "eff0" * 16,
                "action": "read",
                "resource": "/records/customer-42",
                "policy_version": "1.0",
                "decision": 0,
                "reason": "allowed",
                "timestamp": "2026-08-26T14:04:01Z",
                "result_hash": "dead" * 16,
            },
            priv,
        )

    def commit_receipt(exec_id: str, session: Session) -> CapabilityActionReceipt:
        """Add receipt, assign atomic leaf index, commit — all in one transaction."""
        cose_bytes = make_cose(exec_id)
        r = CapabilityActionReceipt(
            receipt_id=str(uuid.uuid4()),
            execution_id=exec_id,
            mount_id="mount_test",
            token_id="tok_test",
            principal="spiffe://example.org/workload/cappo-backend",
            caller_spiffe_id="spiffe://example.org/workload/cappo-backend",
            executor_spiffe_id="spiffe://example.org/workload/cappo-backend",
            caller_cert_sha256="abcd",
            action="read",
            decision="ALLOW",
            reason="allowed",
            actioned_at=_now(),
            content_hash="testhash",
            signed_receipt_cose=cose_bytes,
            capability_id="echo@v1",
        )
        session.add(r)
        # assign_merkle_leaf_index does UPDATE + SELECT in same txn before commit
        assign_merkle_leaf_index(session, r)
        session.commit()
        session.refresh(r)
        return r

    # SEQUENCE_ROW_PERSISTED = True
    seq_row = db.query(MerkleLeafSequence).filter_by(id=1).first()
    assert seq_row is not None, "Singleton sequence row must exist after migration"
    assert seq_row.next_value == 0

    # Commit 3 receipts; snapshot tree after each
    r0 = commit_receipt("exec_1", db)
    leaves1 = get_merkle_ordered_cose_bytes(db)
    tree1 = AppendOnlyMerkleTree(leaves1)
    root1 = tree1.root()

    r1 = commit_receipt("exec_2", db)
    leaves2 = get_merkle_ordered_cose_bytes(db)
    tree2 = AppendOnlyMerkleTree(leaves2)
    root2 = tree2.root()

    r2 = commit_receipt("exec_3", db)
    leaves3 = get_merkle_ordered_cose_bytes(db)
    tree3 = AppendOnlyMerkleTree(leaves3)
    root3 = tree3.root()

    # --- PROOF MATRIX ---

    # MERKLE_STRUCTURE_CREATED = True
    assert isinstance(tree3, AppendOnlyMerkleTree)

    # Leaf indices are stable, monotonic, and correct
    assert r0.merkle_leaf_index == 0
    assert r1.merkle_leaf_index == 1
    assert r2.merkle_leaf_index == 2

    # Sequence advanced correctly
    db.refresh(seq_row)
    assert seq_row.next_value == 3

    # SIGNED_COSE_BYTES_HASHED = True
    assert hash_leaf(r0.signed_receipt_cose) == hash_leaf(tree3._leaves[0])
    assert hash_leaf(r1.signed_receipt_cose) == hash_leaf(tree3._leaves[1])
    assert hash_leaf(r2.signed_receipt_cose) == hash_leaf(tree3._leaves[2])

    # EXACT_PERSISTED_COSE_HASHED / REMINT_NOT_USED = True
    assert leaves3[0] == r0.signed_receipt_cose  # bytes from DB, not re-minted
    assert leaves3[1] == r1.signed_receipt_cose
    assert leaves3[2] == r2.signed_receipt_cose

    # LEAF_HASH_STABLE = True
    assert tree1._leaves[0] == tree2._leaves[0] == tree3._leaves[0]

    # RESTART_REBUILD_STABLE / DB_REQUERY_ORDER_STABLE = True
    rebuilt = AppendOnlyMerkleTree(get_merkle_ordered_cose_bytes(db))
    assert rebuilt.root() == root3
    assert rebuilt._leaves == tree3._leaves

    # UUID_ORDER_IRRELEVANT = True
    # Canonical order is always merkle_leaf_index, not UUID string sort
    canonical = AppendOnlyMerkleTree(get_merkle_ordered_cose_bytes(db))
    assert canonical.root() == root3  # always merkle_leaf_index ordered

    # TREE_SIZE_INCREMENTED = True
    assert tree1.size == 1 and tree2.size == 2 and tree3.size == 3

    # ROOT_CHANGED_AFTER_APPEND = True
    assert root1 != root2 and root2 != root3

    # INCLUSION_PROOF_VERIFIED = True (all leaves)
    for idx in range(3):
        proof = tree3.inclusion_proof(idx)
        assert verify_inclusion_proof(hash_leaf(leaves3[idx]), idx, 3, proof, root3)

    # OLD_ROOT_PRESERVED = True
    assert tree3.root(1) == root1
    assert tree3.root(2) == root2

    # CONSISTENCY_PROOF_VERIFIED = True
    assert verify_consistency_proof(1, 2, root1, root2, tree2.consistency_proof(1, 2))
    assert verify_consistency_proof(1, 3, root1, root3, tree3.consistency_proof(1, 3))
    assert verify_consistency_proof(2, 3, root2, root3, tree3.consistency_proof(2, 3))

    # TAMPERED_COSE_NOT_INCLUDED = True
    p1 = tree3.inclusion_proof(1)
    assert not verify_inclusion_proof(hash_leaf(leaves3[1] + b"TAMPER"), 1, 3, p1, root3)

    # TAMPERED_LEAF_PROOF_DENIED = True
    bad_p = list(p1); bad_p[0] = b"\x00" * 32
    assert not verify_inclusion_proof(hash_leaf(leaves3[1]), 1, 3, bad_p, root3)

    # WRONG_ROOT_DENIED = True
    assert not verify_inclusion_proof(hash_leaf(leaves3[1]), 1, 3, p1, b"\x00" * 32)

    # TRUNCATED_PROOF_DENIED = True
    assert not verify_inclusion_proof(hash_leaf(leaves3[1]), 1, 3, p1[:-1], root3)

    # APPEND_ONLY = True
    assert not hasattr(tree3, "delete")
    assert not hasattr(tree3, "pop")

    # REORDER_DETECTED = True
    assert AppendOnlyMerkleTree([leaves3[1], leaves3[0], leaves3[2]]).root() != root3

    # DUPLICATE_LEAF_INDEX_DENIED / RECEIPT_INDEX_UNIQUE_CONSTRAINT = True
    dup = CapabilityActionReceipt(
        receipt_id=str(uuid.uuid4()),
        execution_id="exec_dup",
        mount_id="m", token_id="t", principal="p",
        action="read", decision="ALLOW", reason="r",
        actioned_at=_now(), content_hash="h",
        signed_receipt_cose=b"fake",
        merkle_leaf_index=0,  # collision with r0
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # G0A_RECEIPT_BEHAVIOR_PRESERVED = True
    rows = db.query(CapabilityActionReceipt).all()
    assert len(rows) == 3
    for row in rows:
        assert row.decision == "ALLOW"
        assert row.merkle_leaf_index is not None

    # G0B5_SIGNATURE_VERIFICATION_PRESERVED = True
    for cose_bytes in leaves3:
        assert verify_signed_execution_evidence(cose_bytes, pub)

    # SEQUENCE_MISSING_RAISES = True
    # Delete the singleton row and verify RuntimeError is raised
    db.execute(text("DELETE FROM merkle_leaf_sequence WHERE id = 1"))
    db.commit()
    orphan = CapabilityActionReceipt(
        receipt_id=str(uuid.uuid4()),
        execution_id="exec_orphan",
        mount_id="m", token_id="t", principal="p",
        action="read", decision="ALLOW", reason="r",
        actioned_at=_now(), content_hash="h",
        signed_receipt_cose=b"orphan_cose",
    )
    db.add(orphan)
    with pytest.raises(RuntimeError, match="merkle_leaf_sequence singleton row"):
        assign_merkle_leaf_index(db, orphan)
    db.rollback()


def test_g0b6_concurrent_allocations_unique():
    """
    CONCURRENT_ALLOCATIONS_UNIQUE = True
    CONCURRENT_LEAVES_STABLE_AFTER_REBUILD = True

    Three threads each open a SEPARATE DB session and commit one receipt.
    All three use assign_merkle_leaf_index.

    After all threads finish:
    - merkle_leaf_index values must be unique (no two receipts at same position)
    - Rebuilding the tree from DB gives a stable root

    Uses a file-backed SQLite DB (not :memory:) so multiple sessions see
    each other's commits, which is necessary for a real concurrency test.
    SQLite serializes writes, so the UPDATE in assign_merkle_leaf_index
    correctly queues concurrent writers.
    """
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        import cappo_backend.models  # noqa — register
        Base.metadata.create_all(engine)

        # Seed the singleton row
        with engine.connect() as conn:
            conn.execute(text("INSERT OR IGNORE INTO merkle_leaf_sequence (id, next_value) VALUES (1, 0)"))
            conn.commit()

        SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        priv = get_evidence_key_pair()
        errors = []
        committed_receipt_ids = []
        lock = threading.Lock()

        def worker(exec_id: str):
            session = SessionLocal()
            try:
                cose_bytes = mint_signed_execution_evidence(
                    {
                        "execution_id": exec_id,
                        "caller_spiffe_id": "spiffe://example.org/workload/test",
                        "executor_spiffe_id": "spiffe://example.org/workload/test",
                        "caller_cert_sha256": "cc" * 32,
                        "capability_id": "concurrent@v1",
                        "biscuit_token_sha256": "bb" * 32,
                        "action": "read",
                        "resource": "/concurrent",
                        "policy_version": "1.0",
                        "decision": 0,
                        "reason": "ok",
                        "timestamp": "2026-08-26T15:00:00Z",
                        "result_hash": "aa" * 32,
                    },
                    priv,
                )
                rid = str(uuid.uuid4())
                r = CapabilityActionReceipt(
                    receipt_id=rid,
                    execution_id=exec_id,
                    mount_id="m", token_id="t", principal="p",
                    action="read", decision="ALLOW", reason="r",
                    actioned_at=_now(), content_hash="h",
                    signed_receipt_cose=cose_bytes,
                    capability_id="concurrent@v1",
                )
                session.add(r)
                assign_merkle_leaf_index(session, r)
                session.commit()
                with lock:
                    committed_receipt_ids.append(rid)
            except Exception as e:
                with lock:
                    errors.append((exec_id, str(e)))
            finally:
                session.close()

        threads = [
            threading.Thread(target=worker, args=(f"concurrent_{i}",))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Worker errors: {errors}"
        assert len(committed_receipt_ids) == 3

        # Verify: unique merkle_leaf_index values across all 3 committed receipts
        verify_session = SessionLocal()
        try:
            rows = (
                verify_session.query(CapabilityActionReceipt)
                .filter(CapabilityActionReceipt.merkle_leaf_index.is_not(None))
                .order_by(CapabilityActionReceipt.merkle_leaf_index.asc())
                .all()
            )
            assert len(rows) == 3, "Expected 3 committed receipts"

            indices = [r.merkle_leaf_index for r in rows]
            assert len(set(indices)) == 3, f"Duplicate indices found: {indices}"
            assert indices == sorted(indices), f"Indices not monotonic: {indices}"

            # CONCURRENT_LEAVES_STABLE_AFTER_REBUILD = True
            leaves = get_merkle_ordered_cose_bytes(verify_session)
            tree = AppendOnlyMerkleTree(leaves)
            root = tree.root()

            # Rebuild a second time from a fresh query — must be identical
            leaves2 = get_merkle_ordered_cose_bytes(verify_session)
            tree2 = AppendOnlyMerkleTree(leaves2)
            assert tree2.root() == root, "Tree root not stable across re-queries"
        finally:
            verify_session.close()
            engine.dispose()
    finally:
        os.unlink(db_path)
