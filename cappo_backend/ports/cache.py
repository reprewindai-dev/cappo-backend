"""Abstract Base Class (Port) for caching operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CachePort(ABC):
    """Port defining cache operations for Gnomledger (PGL)."""

    @abstractmethod
    def get(self, key: str) -> Any | None:
        """Retrieve a value from the cache by key."""
        pass

    @abstractmethod
    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Store a value in the cache with an optional TTL."""
        pass
