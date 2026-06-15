"""Legacy Adapter Router.

Exposes endpoints to manage and monitor legacy enterprise connections.
"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

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

@router.post("/simulate")
async def simulate_event(adapter_type: str) -> Dict[str, Any]:
    """Simulate receiving an event from a legacy adapter."""
    if adapter_type == "snmp":
        if not snmp_adapter.active:
            raise HTTPException(status_code=400, detail="SNMP Adapter is not active")
        raw_data = {"bindings": {"1.3.6.1.2.1.1.3.0": 123456, "1.3.6.1.2.1.2.2.1.8": 1}, "source_ip": "192.168.1.100"}
        normalized = snmp_adapter.normalize_trap(raw_data)
        return {"adapter": "snmp", "raw": raw_data, "normalized": normalized}
        
    elif adapter_type == "modbus":
        if not modbus_adapter.active:
            raise HTTPException(status_code=400, detail="Modbus Adapter is not active")
        raw_data = {40001: 22, 40002: 45, 40003: 120}
        normalized = modbus_adapter.normalize_reading(raw_data)
        return {"adapter": "modbus", "raw": raw_data, "normalized": normalized}
        
    elif adapter_type == "webhook":
        raw_data = {"invoice_id": "INV-5502", "amount": 1500.00, "status": "PAID", "customer": "ACME Corp"}
        normalized = webhook_adapter.normalize_payload("SAP", raw_data)
        return {"adapter": "webhook", "raw": raw_data, "normalized": normalized}
        
    else:
        raise HTTPException(status_code=400, detail=f"Unknown adapter type: {adapter_type}")
