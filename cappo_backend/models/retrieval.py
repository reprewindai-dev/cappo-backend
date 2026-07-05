import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cappo_backend.db.base import Base

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _uuid() -> str:
    return str(uuid.uuid4())

class SourceDocument(Base):
    """
    Source documents for retrieval (e.g., long PDFs).
    """
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False) # e.g. "whitepaper", "contract"
    mime_type: Mapped[str] = mapped_column(String, nullable=False) # e.g. "application/pdf"
    storage_url: Mapped[str] = mapped_column(String, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    indexes = relationship("DocumentIndex", back_populates="document", cascade="all, delete-orphan")
    sessions = relationship("RetrievalSession", back_populates="document", cascade="all, delete-orphan")


class DocumentIndex(Base):
    """
    Tracks the indexing status of a source document in a specific provider (like PageIndex).
    """
    __tablename__ = "document_indexes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("source_documents.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False) # e.g., "pageindex"
    provider_doc_id: Mapped[str] = mapped_column(String, index=True, nullable=True)
    index_type: Mapped[str] = mapped_column(String, nullable=False) # e.g., "tree"
    status: Mapped[str] = mapped_column(String, nullable=False) # e.g., "pending", "completed"
    page_count: Mapped[int] = mapped_column(Integer, nullable=True)
    processing_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    document = relationship("SourceDocument", back_populates="indexes")


class RetrievalSession(Base):
    """
    A specific agent or user interaction requesting retrieval.
    """
    __tablename__ = "retrieval_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("source_documents.id"), nullable=True)
    query: Mapped[str] = mapped_column(String, nullable=False)
    route: Mapped[str] = mapped_column(String, nullable=False) # e.g., "vector", "pageindex", "hybrid"
    tool_used: Mapped[str] = mapped_column(String, nullable=False) # e.g., "doc_reason"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    document = relationship("SourceDocument", back_populates="sessions")
    traces = relationship("RetrievalTrace", back_populates="session", cascade="all, delete-orphan")


class RetrievalTrace(Base):
    """
    The exact evidence and provenance returned by the retrieval provider.
    """
    __tablename__ = "retrieval_traces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("retrieval_sessions.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False) # e.g., "pageindex"
    
    # Use JSON type for broad DB compatibility, but logically structured as arrays/objects
    page_refs: Mapped[dict] = mapped_column(JSON, nullable=True)
    section_refs: Mapped[dict] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    session = relationship("RetrievalSession", back_populates="traces")
