import sqlite3
from datetime import datetime, timezone
from cappo_backend.models.execution_identity import ExecutionIdentity

class AuthorizationError(Exception):
    """Raised when an ExecutionIdentity is invalid or stale."""
    pass

class VeklomVirtualDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _validate_authority(self, identity: ExecutionIdentity):
        if not identity:
            raise AuthorizationError("Missing ExecutionIdentity")
        if identity.revoked:
            raise AuthorizationError("ExecutionIdentity is revoked")
        if identity.expires_at and identity.expires_at < datetime.now(timezone.utc):
            raise AuthorizationError("ExecutionIdentity is expired")

    def connect(self, identity: ExecutionIdentity) -> sqlite3.Connection:
        self._validate_authority(identity)
        
        conn = sqlite3.connect(self.db_path)
        
        # Apply strict memory/OS mappings
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA mmap_size = 30000000000;")
        conn.execute("PRAGMA cache_size = -10000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        
        return conn
