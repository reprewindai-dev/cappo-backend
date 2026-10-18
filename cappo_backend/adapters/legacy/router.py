"""Legacy Adapter Router.

Exposes endpoints to manage and monitor legacy enterprise connections.
"""

from typing import Any, Dict

from fastapi import APIRouter

from cappo_backend.adapters.legacy.modbus_adapter import ModbusAdapter
from cappo_backend.adapters.legacy.snmp_adapter import SNMPAdapter
from cappo_backend.adapters.legacy.webhook_adapter import WebhookAdapter

router = APIRouter(prefix="/legacy", tags=["Legacy Adapters"])

# Singleton instances for demonstration
snmp_adapter = SNMPAdapter()
modbus_adapter = ModbusAdapter()
webhook_adapter = WebhookAdapter()

@router.get("/status")
async def get_adapters_status() -> Dict[str, Any]:
    """Get the status of all legacy adapters."""
    return {
        "snmp": snmp_adapter.get_status(),
        "modbus": modbus_adapter.get_status(),
        "webhook": webhook_adapter.get_status(),
        "total_active": sum([snmp_adapter.active, modbus_adapter.active, webhook_adapter.active])
    }

@router.post("/snmp/toggle")
async def toggle_snmp() -> Dict[str, Any]:
    """Toggle the SNMP adapter on or off."""
    if snmp_adapter.active:
        snmp_adapter.stop_listener()
    else:
        snmp_adapter.start_listener()
    return {"status": "success", "adapter_status": snmp_adapter.get_status()}

@router.post("/modbus/toggle")
async def toggle_modbus() -> Dict[str, Any]:
    """Toggle the Modbus adapter on or off."""
    if modbus_adapter.active:
        modbus_adapter.disconnect()
    else:
        modbus_adapter.connect()
    return {"status": "success", "adapter_status": modbus_adapter.get_status()}

