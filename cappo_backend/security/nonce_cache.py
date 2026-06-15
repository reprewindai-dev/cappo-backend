"""Nonce cache — replay protection for Execution Authorization Tokens.

Each EAT carries a unique nonce.  Before accepting a token the edge gateway
calls ``check_and_store`` to atomically test-and-set the nonce.  If the nonce
has already been consumed the call returns ``True`` (replay detected) and the
gateway rejects the request.

Two implementations are anticipated:

* :class:`InMemoryNonceCache` — adequate for single-process dev/test.
* A future Redis- or DB-backed cache for multi-node production deployments.

Both conform to the :class:`NonceBackend` protocol so consumers remain
decoupled from the storage choice.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class NonceBackend(Protocol):
    """Abstract nonce store for replay detection.

    ``check_and_store`` returns ``True`` when the nonce was **already seen**
    (replay), ``False`` when it is new and has been recorded.
    """

    def check_and_store(self, nonce: str, ttl_seconds: int) -> bool: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryNonceCache:
    """Thread-safe, TTL-aware in-memory nonce cache.

    Expired entries are lazily evicted on every ``check_and_store`` call so the
    dict does not grow unboundedly during long-running processes.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_store(self, nonce: str, ttl_seconds: int) -> bool:
        """Return ``True`` if *nonce* was already consumed (replay detected).

        If the nonce is new it is stored with an expiry of ``ttl_seconds``
        from now and ``False`` is returned.
        """
        now = time.monotonic()

        with self._lock:
            # Lazy eviction of expired entries.
            expired = [k for k, exp in self._seen.items() if exp <= now]
            for k in expired:
                del self._seen[k]

            if nonce in self._seen:
                return True  # replay

            self._seen[nonce] = now + ttl_seconds
            return False  # new nonce — accepted

    def clear(self) -> None:
        """Remove all entries.  Intended for test teardown."""
        with self._lock:
            self._seen.clear()
