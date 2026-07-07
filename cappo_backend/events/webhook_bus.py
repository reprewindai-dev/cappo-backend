"""Agent webhook event bus — fan-out incoming webhook events to registered agent handlers."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]

_handlers: dict[str, list[Handler]] = defaultdict(list)


def register(event_type: str, handler: Handler) -> None:
    """Register an async handler for a given event type."""
    _handlers[event_type].append(handler)


def unregister(event_type: str, handler: Handler) -> None:
    _handlers[event_type] = [h for h in _handlers[event_type] if h is not handler]


async def emit(event_type: str, payload: dict[str, Any]) -> None:
    """Fan-out event to all registered handlers concurrently."""
    handlers = _handlers.get(event_type, [])
    if not handlers:
        logger.debug("No handlers for event_type=%s", event_type)
        return
    results = await asyncio.gather(
        *[h(payload) for h in handlers],
        return_exceptions=True,
    )
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("Handler %s for event %s raised: %s", handlers[i], event_type, result)


async def emit_from_webhook(raw: dict[str, Any]) -> None:
    """Entry point for raw webhook payloads — routes by 'event' key."""
    event_type = raw.get("event") or raw.get("type") or "unknown"
    await emit(str(event_type), raw)
