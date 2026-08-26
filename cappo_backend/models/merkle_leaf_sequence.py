"""
MerkleLeafSequence — single-row sequence table for atomic Merkle leaf index allocation.

One row always exists (id=1). The only valid operation is:
    UPDATE merkle_leaf_sequence SET next_value = next_value + 1 WHERE id = 1

This UPDATE holds a row-level write lock on PostgreSQL (blocks concurrent writers)
and SQLite's exclusive write lock (only one writer at a time). After the UPDATE
commits, read next_value - 1 as the assigned leaf index.

This is the canonical cross-DB safe alternative to SELECT MAX+1, which is
vulnerable to read-modify-write races under concurrent writers.
"""
from __future__ import annotations
from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column
from cappo_backend.db.base import Base


class MerkleLeafSequence(Base):
    __tablename__ = "merkle_leaf_sequence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # next_value = the index that will be assigned to the NEXT receipt.
    # After insert: leaf_index = old next_value; next_value becomes next_value+1.
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
