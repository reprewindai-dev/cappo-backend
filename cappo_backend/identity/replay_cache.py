import time
from typing import Dict, Optional, Any

class ReplayCache:
    """Interface for tracking JTIs to prevent replay attacks."""
    def __init__(self):
        # jti -> expires_at
        self._store: Dict[str, int] = {}
        
    def check_and_store(self, jti: str, expires_at: int) -> bool:
        """
        Returns True if the JTI was successfully stored (not seen before).
        Returns False if the JTI was already in the cache (replayed).
        """
        self._prune()
        if jti in self._store:
            return False
        
        self._store[jti] = expires_at
        return True

    def _prune(self):
        now = int(time.time())
        expired = [k for k, exp in self._store.items() if exp < now]
        for k in expired:
            del self._store[k]

class RedisReplayCache(ReplayCache):
    def __init__(self, redis_client: Any):
        super().__init__()
        self.redis = redis_client

    def check_and_store(self, jti: str, expires_at: int) -> bool:
        now = int(time.time())
        ttl = expires_at - now
        if ttl <= 0:
            return False
        key = f"wid:replay:{jti}"
        # setnx returns 1 if set, 0 if exists
        is_new = self.redis.setnx(key, "1")
        if is_new:
            self.redis.expire(key, ttl)
            return True
        return False

    def _prune(self):
        pass

