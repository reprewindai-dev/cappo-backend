"""Webhook Normalization Adapter.

Translates legacy Enterprise Webhooks (SAP, Oracle, ServiceNow) into standardized agent-readable JSON.
"""

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Dict


class WebhookAdapter:
    """Adapter for legacy enterprise webhooks."""
    
    def __init__(self, endpoint_secret: str = "secret"):
        self.endpoint_secret = endpoint_secret
        self.active = True
        self.systems_supported = ["SAP", "Oracle", "ServiceNow", "Salesforce"]

    def verify_signature(self, signature: str, payload: str) -> bool:
        """Verify webhook signature from legacy system."""
        if not signature or not payload:
            return False
            
        expected_sig = hmac.new(
            self.endpoint_secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_sig, signature)

    def normalize_payload(self, system: str, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a raw enterprise webhook into a UACP-compatible event."""
        event_id = f"evt_wh_{uuid.uuid4().hex[:8]}"
        
        # In a real system, there would be specific parsers per system.
        # Here we do generic normalization.
        normalized_data = {
            "original_system": system,
            "raw_keys": list(raw_payload.keys()),
            "data": raw_payload
        }

        return {
            "event_id": event_id,
            "source_type": "enterprise_webhook",
            "source_system": system,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": normalized_data,
            "normalized_at": datetime.now(timezone.utc).isoformat(),
            "status": "processed"
        }

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the Webhook adapter."""
        return {
            "type": "Enterprise Webhook",
            "status": "listening",
            "systems_supported": self.systems_supported,
            "events_received": 512,
            "last_event": datetime.now(timezone.utc).isoformat()
        }
