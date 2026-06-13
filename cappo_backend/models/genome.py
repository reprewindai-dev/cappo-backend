"""SQLAlchemy models for AI Genomes and parent-child Lineage DAGs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Genome(Base):
    """Immutable record of an AI system's complete configuration footprint.

    Every layer is individually hashed, and the primary key ``genome_hash`` is
    the Merkle root of all five leaf hashes, ensuring tamper-proof immutability.
    """

    __tablename__ = "genomes"

    genome_hash: Mapped[str] = mapped_column(String, primary_key=True)
    genome_id: Mapped[str] = mapped_column(String, index=True, default=_uuid)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Configuration layers stored as JSON.
    model_layer: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_layer: Mapped[dict[str, Any]] = mapped_column(JSON)
    policy_layer: Mapped[dict[str, Any]] = mapped_column(JSON)
    watchtower_layer: Mapped[dict[str, Any]] = mapped_column(JSON)
    task_profile: Mapped[dict[str, Any]] = mapped_column(JSON)

    # Leaf hashes used to construct the Merkle root.
    model_layer_hash: Mapped[str] = mapped_column(String)
    prompt_layer_hash: Mapped[str] = mapped_column(String)
    policy_layer_hash: Mapped[str] = mapped_column(String)
    watchtower_layer_hash: Mapped[str] = mapped_column(String)
    task_profile_hash: Mapped[str] = mapped_column(String)

    # List of leaves for in-browser Merkle verification.
    merkle_proof: Mapped[dict[str, Any]] = mapped_column(JSON)


class GenomeLineage(Base):
    """A directed edge in the Genome Lineage Directed Acyclic Graph (DAG).

    Enables tracking derivation, merges, rollbacks, and multi-agent delegation.
    """

    __tablename__ = "genome_lineage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_genome_hash: Mapped[str] = mapped_column(String, index=True)
    to_genome_hash: Mapped[str] = mapped_column(String, index=True)
    relationship_type: Mapped[str] = mapped_column(String)  # DERIVED_FROM, MERGED_FROM, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
