"""Abstract Base Class (Port) for Graph/Lineage operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GraphPort(ABC):
    """Port defining the graph lineage operations for AI Genomes."""

    @abstractmethod
    def add_edge(
        self, from_genome_hash: str, to_genome_hash: str, relationship_type: str
    ) -> None:
        """Create a directed lineage edge from a parent to a child genome."""
        pass

    @abstractmethod
    def get_ancestors(self, genome_hash: str) -> list[dict[str, Any]]:
        """Retrieve all ancestor genome records of a specific genome hash."""
        pass

    @abstractmethod
    def get_descendants(self, genome_hash: str) -> list[dict[str, Any]]:
        """Retrieve all descendant genome records of a specific genome hash."""
        pass
