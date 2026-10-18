"""LicenseKey model — cappo-backend acts as the license authority for Veklom."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from cappo_backend.db.base import Base


class LicenseKey(Base):
    __tablename__ = "license_keys"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String(128), unique=True, nullable=False, index=True)
    key_hash = Column(String(64), nullable=False, unique=True)  # SHA-256 of raw key
    workspace_id = Column(String(128), nullable=True, index=True)
    plan_tier = Column(String(64), nullable=False, default="starter")
    status = Column(String(32), nullable=False, default="issued")  # issued|active|revoked|expired
    issued_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(Text, nullable=True)
    max_activations = Column(Integer, default=1)
    activation_count = Column(Integer, default=0)
    issued_by = Column(String(128), nullable=True)
    metadata_json = Column(Text, nullable=True)  # JSON blob for extra fields
