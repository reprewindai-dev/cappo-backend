"""Abstract Base Class (Port) for event queue/publish operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class QueuePort(ABC):
    """Port defining event queue operations for PGL lifecycle events."""

    @abstractmethod
    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish an event to the specified topic."""
        pass

    @abstractmethod
    def subscribe(self, topic: str, handler: Any) -> None:
        """Subscribe a handler to a topic."""
        pass
