"""Abstract Base Class (Port) for genome configuration metadata storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorePort(ABC):
    """Port defining metadata persistence operations for AI Genomes."""

    @abstractmethod
    def save_genome(self, genome_hash: str, genome_data: dict[str, Any]) -> None:
        """Persist a new genome record into the registry."""
        pass

    @abstractmethod
    def get_genome(self, genome_hash: str) -> dict[str, Any] | None:
        """Fetch a genome record by its Merkle root hash."""
        pass
