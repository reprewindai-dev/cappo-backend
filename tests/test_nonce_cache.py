"""Tests for the in-memory nonce cache (replay protection)."""

from __future__ import annotations

import time

from cappo_backend.security.nonce_cache import InMemoryNonceCache


class TestInMemoryNonceCache:
    """InMemoryNonceCache correctness tests."""

    def test_new_nonce_returns_false(self):
        """A never-seen nonce should return False (not a replay)."""
        cache = InMemoryNonceCache()
        assert cache.check_and_store("nonce-1", ttl_seconds=60) is False

    def test_seen_nonce_returns_true(self):
        """A previously stored nonce should return True (replay detected)."""
        cache = InMemoryNonceCache()
        cache.check_and_store("nonce-1", ttl_seconds=60)
        assert cache.check_and_store("nonce-1", ttl_seconds=60) is True

    def test_different_nonces_independent(self):
        """Different nonces don't interfere with each other."""
        cache = InMemoryNonceCache()
        assert cache.check_and_store("nonce-a", ttl_seconds=60) is False
        assert cache.check_and_store("nonce-b", ttl_seconds=60) is False
        assert cache.check_and_store("nonce-a", ttl_seconds=60) is True
        assert cache.check_and_store("nonce-b", ttl_seconds=60) is True

    def test_expired_nonce_is_evicted(self):
        """A nonce stored with a tiny TTL should be evicted after expiry."""
        cache = InMemoryNonceCache()
        # Store with a 0-second TTL (expires immediately)
        cache.check_and_store("nonce-expire", ttl_seconds=0)
        # Give it a moment to expire
        time.sleep(0.01)
        # Should be treated as new (evicted)
        assert cache.check_and_store("nonce-expire", ttl_seconds=60) is False

    def test_clear_removes_all_entries(self):
        """clear() should empty the cache."""
        cache = InMemoryNonceCache()
        cache.check_and_store("nonce-1", ttl_seconds=60)
        cache.check_and_store("nonce-2", ttl_seconds=60)
        cache.clear()
        # Both should be treated as new after clear
        assert cache.check_and_store("nonce-1", ttl_seconds=60) is False
        assert cache.check_and_store("nonce-2", ttl_seconds=60) is False

    def test_eviction_does_not_remove_live_entries(self):
        """Eviction sweep should only remove expired entries."""
        cache = InMemoryNonceCache()
        # Store one with short TTL, one with long TTL
        cache.check_and_store("short-lived", ttl_seconds=0)
        cache.check_and_store("long-lived", ttl_seconds=300)
        time.sleep(0.01)
        # Trigger eviction by storing another
        cache.check_and_store("trigger", ttl_seconds=60)
        # short-lived should be evicted, long-lived should remain
        assert cache.check_and_store("short-lived", ttl_seconds=60) is False
        assert cache.check_and_store("long-lived", ttl_seconds=60) is True
