"""Model for storing tenant-specific LLM provider API keys."""

from sqlalchemy import Column, String, DateTime, Integer, Boolean, JSON, UniqueConstraint
from cappo_backend.db.base import Base
from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc)

class TenantProviderCredential(Base):
    __tablename__ = "tenant_provider_credentials"

    id = Column(String, primary_key=True)
    workspace_id = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False, index=True)  # e.g., "openai", "ollama"
    credential_profile = Column(String, nullable=False, default="default")
    auth_type = Column(String, nullable=False, default="bearer")  # e.g., "bearer", "none"
    encrypted_secret = Column(String, nullable=True)
    base_url = Column(String, nullable=True)
    key_version = Column(Integer, default=1)
    
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    disabled_at = Column(DateTime(timezone=True), nullable=True)
    
    metadata_json = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", "credential_profile", name="uix_workspace_provider_profile"),
    )

    def __repr__(self) -> str:
        return f"<TenantProviderCredential(workspace_id='{self.workspace_id}', provider='{self.provider}', profile='{self.credential_profile}')>"
