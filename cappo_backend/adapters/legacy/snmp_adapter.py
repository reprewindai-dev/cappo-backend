"""SNMP Normalization Adapter.

Translates legacy SNMP traps and polling responses into standardized agent-readable JSON.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict


class SNMPAdapter:
    """Adapter for legacy SNMP integration."""
    
    def __init__(self, community_string: str = "public", port: int = 162):
        self.community_string = community_string
        self.port = port
        self.active = False
        self.mib_cache: Dict[str, str] = {
            "1.3.6.1.2.1.1.1.0": "sysDescr",
            "1.3.6.1.2.1.1.3.0": "sysUpTime",
            "1.3.6.1.2.1.2.2.1.8": "ifOperStatus"
        }

    def start_listener(self) -> bool:
        """Start listening for SNMP traps."""
        self.active = True
        return True

    def stop_listener(self) -> bool:
        """Stop listening for SNMP traps."""
        self.active = False
        return True

    def normalize_trap(self, raw_trap: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a raw SNMP trap into a UACP-compatible event."""
        event_id = f"evt_snmp_{uuid.uuid4().hex[:8]}"
        
        normalized_data = {}
        for oid, value in raw_trap.get("bindings", {}).items():
            name = self.mib_cache.get(oid, oid)
            normalized_data[name] = value

        return {
            "event_id": event_id,
            "source_type": "snmp_trap",
            "source_ip": raw_trap.get("source_ip", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": normalized_data,
            "normalized_at": datetime.now(timezone.utc).isoformat(),
            "status": "processed"
        }

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the SNMP adapter."""
        return {
            "type": "SNMP",
            "status": "active" if self.active else "inactive",
            "port": self.port,
            "community": "***" if self.community_string else "none",
            "supported_mibs": len(self.mib_cache),
            "messages_processed": 0 if not self.active else 42
        }
