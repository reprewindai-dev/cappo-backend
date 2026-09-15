import sqlite3
from datetime import datetime, timezone
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.capability_lease import CapabilityLease

class AuthorizationError(Exception):
    """Raised when an ExecutionIdentity is invalid or stale."""
    pass

class VeklomVirtualDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _validate_authority(self, identity: ExecutionIdentity | CapabilityLease):
        if not identity:
            raise AuthorizationError("Missing ExecutionIdentity or CapabilityLease")
            
        if isinstance(identity, ExecutionIdentity):
            if identity.revoked:
                raise AuthorizationError("ExecutionIdentity is revoked")
            if identity.expires_at and identity.expires_at < datetime.now(timezone.utc):
                raise AuthorizationError("ExecutionIdentity is expired")
        elif isinstance(identity, CapabilityLease):
            if identity.lease_state in ["REVOKED", "EXPIRED"]:
                raise AuthorizationError(f"CapabilityLease is {identity.lease_state.lower()}")
            if identity.expires_at and identity.expires_at < datetime.now(timezone.utc):
                raise AuthorizationError("CapabilityLease is expired")
        else:
            raise AuthorizationError("Invalid authority object type")

    def connect(self, identity: ExecutionIdentity | CapabilityLease) -> sqlite3.Connection:
        self._validate_authority(identity)
        
        conn = sqlite3.connect(self.db_path)
        
        # Apply strict memory/OS mappings
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA mmap_size = 30000000000;")
        conn.execute("PRAGMA cache_size = -10000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        
        return conn
