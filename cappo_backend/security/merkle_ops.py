"""
cappo_backend/security/merkle_ops.py

Atomic Merkle leaf index allocation via a dedicated sequence table.

ALLOCATION CONTRACT (all steps in ONE transaction, never split across commits):

    BEGIN TRANSACTION

    UPDATE merkle_leaf_sequence SET next_value = next_value + 1 WHERE id = 1
    -- This acquires a row-level write lock (PostgreSQL) or the exclusive write
    -- lock (SQLite). Concurrent writers queue behind this lock.

    SELECT next_value FROM merkle_leaf_sequence WHERE id = 1
    -- Read the ALREADY-INCREMENTED value inside the SAME transaction, while the
    -- lock is held. assigned_index = next_value - 1.

    INSERT INTO capability_action_receipts (..., merkle_leaf_index = assigned_index)

    COMMIT
    -- Sequence increment and receipt row commit atomically.
    -- If INSERT fails, the ROLLBACK also rolls back the sequence increment,
    -- making that index position permanently unissued (a gap). Gaps are allowed.
    -- What is prohibited: a committed index being reused.

DO NOT read next_value after commit. Any read outside this transaction may
observe a value written by a concurrent writer, not the value you incremented.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from cappo_backend.models import CapabilityActionReceipt


def assign_merkle_leaf_index(db: Session, receipt: CapabilityActionReceipt) -> int:
    """
    Allocate the next Merkle leaf index for receipt and assign it.

    MUST be called inside the same transaction that will commit the receipt row.
    MUST be called after db.add(receipt) but BEFORE db.commit().

    Returns the assigned index so callers can log/record it.

    Raises RuntimeError if the sequence singleton row (id=1) is missing.
    This is intentional: a missing sequence row is a configuration failure
    that must not silently reinitialize (which could cause index reuse).
    """
    if not receipt.signed_receipt_cose:
        return  # no COSE bytes → no Merkle commitment

    # Step 1: increment the sequence counter. This acquires the row-level
    # write lock on PostgreSQL or the exclusive write lock on SQLite.
    result = db.execute(
        text("UPDATE merkle_leaf_sequence SET next_value = next_value + 1 WHERE id = 1")
    )
    if result.rowcount == 0:
        raise RuntimeError(
            "merkle_leaf_sequence singleton row (id=1) is missing. "
            "Run the seeding migration or call seed_merkle_sequence(db). "
            "Do NOT silently reinitialize — that risks index reuse."
        )

    # Step 2: read the ALREADY-INCREMENTED value inside the SAME transaction.
    # The write lock is still held. assigned_index = new_value - 1.
    row = db.execute(
        text("SELECT next_value FROM merkle_leaf_sequence WHERE id = 1")
    ).fetchone()
    assigned_index = int(row[0]) - 1

    receipt.merkle_leaf_index = assigned_index
    return assigned_index


def seed_merkle_sequence(db: Session) -> None:
    """
    Ensure the singleton sequence row exists (id=1, next_value=0).

    Safe to call multiple times — uses INSERT OR IGNORE (SQLite) or
    INSERT ... ON CONFLICT DO NOTHING (PostgreSQL).
    ONLY for use during initial schema setup or test fixtures.
    NEVER call this to reset the sequence on an existing database.
    """
    db.execute(
        text(
            "INSERT INTO merkle_leaf_sequence (id, next_value) VALUES (1, 0) "
            "ON CONFLICT (id) DO NOTHING"
        )
    )
    db.commit()


def get_merkle_ordered_cose_bytes(db: Session) -> list[bytes]:
    """
    Return all signed_receipt_cose bytes in canonical Merkle leaf order.

    ORDER BY merkle_leaf_index ASC is the ONLY valid Merkle reconstruction order.
    UUID sort and timestamp sort are both forbidden for this purpose.

    Skips rows where signed_receipt_cose or merkle_leaf_index is NULL.
    """
    rows = (
        db.query(CapabilityActionReceipt)
        .filter(
            CapabilityActionReceipt.merkle_leaf_index.is_not(None),
            CapabilityActionReceipt.signed_receipt_cose.is_not(None),
        )
        .order_by(CapabilityActionReceipt.merkle_leaf_index.asc())
        .all()
    )
    return [r.signed_receipt_cose for r in rows]
