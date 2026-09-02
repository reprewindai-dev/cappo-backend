"""ConsequenceEvidence ?" Cryptographic proof of target-side execution.

This model separates authorization (CapabilityActionReceipt) from
execution truth (ConsequenceEvidence).

An authorization receipt proves that authority existed.
It does not prove the external mutation occurred.
ConsequenceEvidence must be originated or countersigned by the target
to prove that the consequence actually took place.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConsequenceEvidence(Base):
    __tablename__ = "consequence_evidence"

    evidence_id: Mapped[str] = mapped_column(String, primary_key=True)
    
    # Link to the authority that permitted this
    receipt_id: Mapped[str] = mapped_column(String, index=True)
    operation_id: Mapped[str] = mapped_column(String, index=True)
    
    # Target-side origin identification
    target_identity: Mapped[str] = mapped_column(String, index=True)
    target_nonce: Mapped[str] = mapped_column(String, unique=True)
    
    # The actual consequence output/state asserted by the target
    asserted_truth_state: Mapped[str] = mapped_column(Text)
    
    # Cryptographic proof originated or countersigned by the target
    target_signature_cose: Mapped[bytes] = mapped_column(LargeBinary)
    target_public_key_hash: Mapped[str] = mapped_column(String)
    
    # Hashes for idempotency and chaining
    proof_subject_hash: Mapped[str] = mapped_column(String)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
