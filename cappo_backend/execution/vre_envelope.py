import hashlib
import json
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class VREEnvelopeSpec:
    """Canonical VRE Envelope specification for exact commitment."""
    execution_id: str
    mount_id: str
    action: str
    resource: str
    memory_max_bytes: int
    compute_fuel_units: int
    wall_deadline_ms: int
    runtime_kind: str
    allow_network: bool = False

    def compute_digest(self) -> str:
        """Computes deterministic SHA-256 digest of normalized envelope configuration."""
        canonical_json = json.dumps({
            "action": self.action,
            "allow_network": self.allow_network,
            "compute_fuel_units": self.compute_fuel_units,
            "execution_id": self.execution_id,
            "memory_max_bytes": self.memory_max_bytes,
            "mount_id": self.mount_id,
            "resource": self.resource,
            "runtime_kind": self.runtime_kind,
            "wall_deadline_ms": self.wall_deadline_ms
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()
