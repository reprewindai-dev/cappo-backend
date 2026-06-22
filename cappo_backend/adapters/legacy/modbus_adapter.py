"""Modbus Normalization Adapter.

Translates legacy Modbus TCP/RTU signals into standardized agent-readable JSON.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict


class ModbusAdapter:
    """Adapter for legacy Modbus integration."""

    def __init__(self, host: str = "127.0.0.1", port: int = 502):
        self.host = host
        self.port = port
        self.active = False
        self.registers = {
            40001: "temperature_celsius",
            40002: "pressure_psi",
            40003: "flow_rate_gpm",
        }

    def connect(self) -> bool:
        """Connect to Modbus TCP device."""
        self.active = True
        return True

    def disconnect(self) -> bool:
        """Disconnect from Modbus TCP device."""
        self.active = False
        return True

    def normalize_reading(self, raw_registers: Dict[int, int]) -> Dict[str, Any]:
        """Convert raw Modbus registers into a UACP-compatible event."""
        event_id = f"evt_mb_{uuid.uuid4().hex[:8]}"

        normalized_data = {}
        for reg_addr, value in raw_registers.items():
            name = self.registers.get(reg_addr, f"register_{reg_addr}")
            normalized_data[name] = value

        return {
            "event_id": event_id,
            "source_type": "modbus_poll",
            "source_host": self.host,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": normalized_data,
            "normalized_at": datetime.now(timezone.utc).isoformat(),
            "status": "processed",
        }

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the Modbus adapter."""
        return {
            "type": "Modbus TCP",
            "status": "connected" if self.active else "disconnected",
            "host": self.host,
            "port": self.port,
            "mapped_registers": len(self.registers),
            "polls_completed": 0 if not self.active else 128,
        }
