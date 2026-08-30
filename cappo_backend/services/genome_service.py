"""GenomeService — core PGL genome lifecycle operations.

Handles genome registration (Merkle tree construction), diffing (RFC 6902-style
JSON Patch), lineage retrieval, and birth-certificate minting. All persistence
is delegated through hexagonal ports so the service is storage-agnostic.
"""

from __future__ import annotations

import hashlib
from typing import Any

from cappo_backend.ports.cache import CachePort
from cappo_backend.ports.graph import GraphPort
from cappo_backend.ports.queue import QueuePort
from cappo_backend.ports.store import StorePort
from cappo_backend.services.canonical import canonical_json, sha256_json

# Layer ordering is fixed so the Merkle tree is deterministic.
_LAYER_NAMES: list[str] = [
    "model_layer",
    "prompt_layer",
    "policy_layer",
    "watchtower_layer",
    "task_profile",
]


class GenomeService:
    """Facade for all Genome-related PGL operations.

    Parameters
    ----------
    store : StorePort
        Persistence adapter for genome records.
    graph : GraphPort
        Lineage-DAG adapter.
    cache : CachePort
        Lookup cache for hot-path reads.
    queue : QueuePort
        Event-bus adapter for lifecycle notifications.
    """

    def __init__(
        self,
        store: StorePort,
        graph: GraphPort,
        cache: CachePort,
        queue: QueuePort,
    ) -> None:
        self._store = store
        self._graph = graph
        self._cache = cache
        self._queue = queue

    # ------------------------------------------------------------------
    # Merkle helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_pair(left: str, right: str) -> str:
        """SHA-256 of the concatenation of two hex-digest strings."""
        combined = (left + right).encode("utf-8")
        return hashlib.sha256(combined).hexdigest()

    @staticmethod
    def compute_leaf_hashes(layers: dict[str, Any]) -> dict[str, str]:
        """Compute SHA-256 leaf hashes for each of the five config layers."""
        return {
            f"{name}_hash": sha256_json(layers[name])
            for name in _LAYER_NAMES
        }

    @classmethod
    def compute_merkle_root(cls, leaf_hashes: list[str]) -> str:
        """Build a Merkle tree from leaf hashes and return the root.

        If the number of nodes at a level is odd, the last node is promoted
        without pairing (standard binary Merkle approach).
        """
        if not leaf_hashes:
            raise ValueError("Cannot compute Merkle root from zero leaves")

        level = list(leaf_hashes)
        while len(level) > 1:
            next_level: list[str] = []
            for i in range(0, len(level), 2):
                if i + 1 < len(level):
                    next_level.append(cls._hash_pair(level[i], level[i + 1]))
                else:
                    next_level.append(level[i])  # odd leaf promoted
            level = next_level
        return level[0]

    @classmethod
    def build_merkle_proof(cls, leaf_hashes: list[str]) -> dict[str, Any]:
        """Build the full Merkle proof structure (leaves + intermediate nodes)."""
        intermediates: list[str] = []
        level = list(leaf_hashes)
        while len(level) > 1:
            next_level: list[str] = []
            for i in range(0, len(level), 2):
                if i + 1 < len(level):
                    parent = cls._hash_pair(level[i], level[i + 1])
                else:
                    parent = level[i]
                next_level.append(parent)
                intermediates.append(parent)
            level = next_level

        return {
            "leaves": leaf_hashes,
            "intermediates": intermediates,
            "root": level[0] if level else None,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_genome(
        self,
        *,
        model_layer: dict[str, Any],
        prompt_layer: dict[str, Any],
        policy_layer: dict[str, Any],
        watchtower_layer: dict[str, Any],
        task_profile: dict[str, Any],
        parent_genome_hash: str | None = None,
        relationship_type: str = "DERIVED_FROM",
    ) -> dict[str, Any]:
        """Register a new genome, computing Merkle root and persisting.

        Returns
        -------
        dict
            Genome record dict including ``genome_hash`` and ``birth_certificate``.
        """
        layers = {
            "model_layer": model_layer,
            "prompt_layer": prompt_layer,
            "policy_layer": policy_layer,
            "watchtower_layer": watchtower_layer,
            "task_profile": task_profile,
        }

        leaf_hash_map = self.compute_leaf_hashes(layers)
        leaf_list = [leaf_hash_map[f"{name}_hash"] for name in _LAYER_NAMES]
        merkle_root = self.compute_merkle_root(leaf_list)
        merkle_proof = self.build_merkle_proof(leaf_list)

        genome_data: dict[str, Any] = {
            **layers,
            **leaf_hash_map,
            "merkle_proof": merkle_proof,
        }

        # Persist
        self._store.save_genome(merkle_root, genome_data)

        # Lineage edge
        if parent_genome_hash:
            self._graph.add_edge(parent_genome_hash, merkle_root, relationship_type)

        # Cache
        genome_record = self._store.get_genome(merkle_root)
        if genome_record:
            self._cache.set(f"genome:{merkle_root}", genome_record, ttl_seconds=300)

        # Lifecycle event
        self._queue.publish("genome.registered", {
            "genome_hash": merkle_root,
            "parent_genome_hash": parent_genome_hash,
        })

        birth_cert = self.mint_birth_certificate(merkle_root)

        return {
            "genome_hash": merkle_root,
            "birth_certificate": birth_cert,
        }

    def get_genome(self, genome_hash: str) -> dict[str, Any] | None:
        """Fetch a genome, checking cache first."""
        cached = self._cache.get(f"genome:{genome_hash}")
        if cached is not None:
            return cached
        record = self._store.get_genome(genome_hash)
        if record:
            self._cache.set(f"genome:{genome_hash}", record, ttl_seconds=300)
        return record

    def diff_genomes(self, hash_a: str, hash_b: str) -> list[dict[str, Any]]:
        """Compute an RFC 6902-style JSON Patch between two genomes.

        Returns a list of patch operations representing the differences between
        the layer data of genome *hash_a* and genome *hash_b*.
        """
        genome_a = self.get_genome(hash_a)
        genome_b = self.get_genome(hash_b)
        if genome_a is None:
            raise ValueError(f"Genome not found: {hash_a}")
        if genome_b is None:
            raise ValueError(f"Genome not found: {hash_b}")

        patches: list[dict[str, Any]] = []
        for layer in _LAYER_NAMES:
            val_a = genome_a.get(layer)
            val_b = genome_b.get(layer)
            if canonical_json(val_a) != canonical_json(val_b):
                patches.append({
                    "op": "replace",
                    "path": f"/{layer}",
                    "old_value": val_a,
                    "value": val_b,
                })
        return patches

    def get_lineage(self, genome_hash: str) -> dict[str, Any]:
        """Return the full lineage for a genome (ancestors + descendants)."""
        ancestors = self._graph.get_ancestors(genome_hash)
        descendants = self._graph.get_descendants(genome_hash)
        return {
            "genome_hash": genome_hash,
            "ancestors": ancestors,
            "descendants": descendants,
        }

    def mint_birth_certificate(self, genome_hash: str) -> dict[str, Any]:
        """Mint a birth certificate for an existing genome.

        Returns
        -------
        dict
            Contains the genome hash, all layer data, Merkle proof, and
            the ``created_at`` timestamp.
        """
        record = self.get_genome(genome_hash)
        if record is None:
            raise ValueError(f"Genome not found: {genome_hash}")

        return {
            "genome_hash": record["genome_hash"],
            "layers": {name: record[name] for name in _LAYER_NAMES},
            "leaf_hashes": {
                f"{name}_hash": record[f"{name}_hash"] for name in _LAYER_NAMES
            },
            "merkle_proof": record["merkle_proof"],
            "created_at": record.get("created_at"),
        }

    def list_genomes(self, offset: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        """Paginated genome listing — delegates to the store adapter."""
        # The StorePort base class doesn't mandate list_genomes, but our
        # SQLiteStoreAdapter provides it. Use duck typing for forward compat.
        if hasattr(self._store, "list_genomes"):
            return self._store.list_genomes(offset=offset, limit=limit)  # type: ignore[attr-defined]
        return []
