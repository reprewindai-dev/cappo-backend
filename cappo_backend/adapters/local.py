"""Local-development adapter implementations for all four PGL ports.

These adapters are designed for single-process, SQLite-backed development and
testing. Production deployments should swap these for Redis/Kafka/Neo4j adapters
that implement the same port interfaces.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from cappo_backend.models.genome import Genome, GenomeLineage
from cappo_backend.ports.cache import CachePort
from cappo_backend.ports.graph import GraphPort
from cappo_backend.ports.queue import QueuePort
from cappo_backend.ports.store import StorePort


# ---------------------------------------------------------------------------
# InMemoryCacheAdapter
# ---------------------------------------------------------------------------


class InMemoryCacheAdapter(CachePort):
    """Dict-based cache with optional per-key TTL (seconds).

    Expired entries are lazily evicted on ``get``.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float | None]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = (time.monotonic() + ttl_seconds) if ttl_seconds else None
        self._store[key] = (value, expires_at)


# ---------------------------------------------------------------------------
# SQLiteStoreAdapter
# ---------------------------------------------------------------------------


class SQLiteStoreAdapter(StorePort):
    """Persists and retrieves Genome records via the existing SQLAlchemy Session."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def save_genome(self, genome_hash: str, genome_data: dict[str, Any]) -> None:
        """Create and persist a :class:`Genome` model record."""
        record = Genome(
            genome_hash=genome_hash,
            genome_id=genome_data.get("genome_id", genome_hash),
            version=genome_data.get("version", 1),
            model_layer=genome_data["model_layer"],
            prompt_layer=genome_data["prompt_layer"],
            policy_layer=genome_data["policy_layer"],
            watchtower_layer=genome_data["watchtower_layer"],
            task_profile=genome_data["task_profile"],
            model_layer_hash=genome_data["model_layer_hash"],
            prompt_layer_hash=genome_data["prompt_layer_hash"],
            policy_layer_hash=genome_data["policy_layer_hash"],
            watchtower_layer_hash=genome_data["watchtower_layer_hash"],
            task_profile_hash=genome_data["task_profile_hash"],
            merkle_proof=genome_data["merkle_proof"],
        )
        self._db.add(record)
        self._db.flush()

    def get_genome(self, genome_hash: str) -> dict[str, Any] | None:
        record = self._db.get(Genome, genome_hash)
        if record is None:
            return None
        return {
            "genome_hash": record.genome_hash,
            "genome_id": record.genome_id,
            "version": record.version,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "model_layer": record.model_layer,
            "prompt_layer": record.prompt_layer,
            "policy_layer": record.policy_layer,
            "watchtower_layer": record.watchtower_layer,
            "task_profile": record.task_profile,
            "model_layer_hash": record.model_layer_hash,
            "prompt_layer_hash": record.prompt_layer_hash,
            "policy_layer_hash": record.policy_layer_hash,
            "watchtower_layer_hash": record.watchtower_layer_hash,
            "task_profile_hash": record.task_profile_hash,
            "merkle_proof": record.merkle_proof,
        }

    def list_genomes(self, offset: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        """Return a paginated list of genome summaries (newest first)."""
        records = (
            self._db.query(Genome)
            .order_by(Genome.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "genome_hash": r.genome_hash,
                "genome_id": r.genome_id,
                "version": r.version,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]


# ---------------------------------------------------------------------------
# SQLiteGraphAdapter
# ---------------------------------------------------------------------------


class SQLiteGraphAdapter(GraphPort):
    """Lineage DAG operations backed by the ``genome_lineage`` table.

    Ancestor and descendant queries use recursive CTEs via :func:`sqlalchemy.text`
    for correct DAG traversal over SQLite.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def add_edge(
        self, from_genome_hash: str, to_genome_hash: str, relationship_type: str
    ) -> None:
        record = GenomeLineage(
            from_genome_hash=from_genome_hash,
            to_genome_hash=to_genome_hash,
            relationship_type=relationship_type,
        )
        self._db.add(record)
        self._db.flush()

    def get_ancestors(self, genome_hash: str) -> list[dict[str, Any]]:
        """Walk the lineage DAG upward using a recursive CTE."""
        sql = text(
            """
            WITH RECURSIVE ancestors(hash, depth) AS (
                SELECT gl.from_genome_hash, 1
                FROM genome_lineage gl
                WHERE gl.to_genome_hash = :hash
              UNION ALL
                SELECT gl.from_genome_hash, a.depth + 1
                FROM genome_lineage gl
                JOIN ancestors a ON gl.to_genome_hash = a.hash
            )
            SELECT DISTINCT g.genome_hash,
                            g.genome_id,
                            g.version,
                            g.created_at,
                            a.depth
            FROM ancestors a
            JOIN genomes g ON g.genome_hash = a.hash
            ORDER BY a.depth
            """
        )
        rows = self._db.execute(sql, {"hash": genome_hash}).fetchall()
        return [
            {
                "genome_hash": row[0],
                "genome_id": row[1],
                "version": row[2],
                "created_at": str(row[3]) if row[3] else None,
                "depth": row[4],
            }
            for row in rows
        ]

    def get_descendants(self, genome_hash: str) -> list[dict[str, Any]]:
        """Walk the lineage DAG downward using a recursive CTE."""
        sql = text(
            """
            WITH RECURSIVE descendants(hash, depth) AS (
                SELECT gl.to_genome_hash, 1
                FROM genome_lineage gl
                WHERE gl.from_genome_hash = :hash
              UNION ALL
                SELECT gl.to_genome_hash, d.depth + 1
                FROM genome_lineage gl
                JOIN descendants d ON gl.from_genome_hash = d.hash
            )
            SELECT DISTINCT g.genome_hash,
                            g.genome_id,
                            g.version,
                            g.created_at,
                            d.depth
            FROM descendants d
            JOIN genomes g ON g.genome_hash = d.hash
            ORDER BY d.depth
            """
        )
        rows = self._db.execute(sql, {"hash": genome_hash}).fetchall()
        return [
            {
                "genome_hash": row[0],
                "genome_id": row[1],
                "version": row[2],
                "created_at": str(row[3]) if row[3] else None,
                "depth": row[4],
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# DirectQueueAdapter
# ---------------------------------------------------------------------------


class DirectQueueAdapter(QueuePort):
    """Synchronous in-process event dispatcher.

    Handlers registered via ``subscribe`` are invoked immediately and
    synchronously when ``publish`` is called — suitable for local dev and tests.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = defaultdict(list)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers.get(topic, []):
            handler(payload)

    def subscribe(self, topic: str, handler: Any) -> None:
        self._handlers[topic].append(handler)
