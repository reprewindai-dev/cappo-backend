"""AuthorityRollbackWitness ?" PGL Anchoring and Recovery invariant.

This model serves as the local mirror of the external PGL rollback witness.
It proves that an authority consumption event was witnessed outside this
database's mutable state boundary.

Recovery Invariant:
An authority generation that has been externally observed as consumed cannot
subsequently become executable, even after restoration of all local mutable state.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuthorityRollbackWitness(Base):
    __tablename__ = "authority_rollback_witnesses"

    # UUID of this witness record
    witness_id: Mapped[str] = mapped_column(String, primary_key=True)
    
    # Core linkage to the consumed authority
    receipt_id: Mapped[str] = mapped_column(String, index=True)
    authority_id: Mapped[str] = mapped_column(String, index=True)
    
    # Cryptographic commitments
    request_commitment: Mapped[str] = mapped_column(String)
    receipt_hash: Mapped[str] = mapped_column(String)
    previous_commitment: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Monotonic generation counters protecting against DB snapshot restore
    authority_generation: Mapped[int] = mapped_column(Integer)
    policy_generation: Mapped[int] = mapped_column(Integer)
    fencing_generation: Mapped[int] = mapped_column(Integer)

    # When this witness was explicitly synchronized with the external PGL anchor
    anchored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
