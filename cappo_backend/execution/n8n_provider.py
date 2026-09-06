import logging
from typing import Any, Dict

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class ExecutionEnvelope(BaseModel):
    execution_id: str
    execution_identity: str
    capability_id: str
    lease_id: str
    policy_digest: str
    allowed_resources: list[str]
    allowed_network: list[str]
    allowed_data: list[str]
    budget: Dict[str, Any]
    expiry: int
    delegation_depth: int
    nonce: str
    evidence_parent: str
    signature: str

class N8nExecutionProvider:
    """
    Veklom Execution Provider for n8n.
    This acts as the bridge between Veklom's strict authority boundary (CAPPO)
    and n8n's execution substrate.
    """
    
    def __init__(self, n8n_base_url: str = "http://localhost:5678"):
        self.base_url = n8n_base_url.rstrip('/')
        
    async def discover_capabilities(self) -> dict:
        # Check health and get version
        health = await self.health()
        return {
            "provider": "n8n",
            "status": "active" if health.get("status") == "ok" else "unavailable",
            "version": health.get("version", "unknown")
        }

    async def prepare_execution(self, envelope: ExecutionEnvelope) -> dict:
        """
        Prepare an n8n workflow execution bounded by the Veklom envelope.
        In reality, this would configure webhook payloads or execution parameters.
        """
        logger.info(f"Preparing n8n execution for capability {envelope.capability_id}")
        return {"status": "prepared", "execution_id": envelope.execution_id}

    async def invoke(self, envelope: ExecutionEnvelope, webhook_id: str, payload: dict) -> dict:
        """
        Invoke a specific n8n webhook/workflow.
        The envelope parameters are passed as the payload or headers to ensure
        the downstream n8n nodes are bound by the authority.
        """
        import json
        url = f"{self.base_url}/webhook/{webhook_id}"
        
        # Attach the bounded authority to the request as a JSON string 
        # so n8n can easily forward it as an HTTP header.
        bounded_payload = {
            "veklom_authority": json.dumps(envelope.model_dump()),
            "data": payload
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=bounded_payload)
                response.raise_for_status()
                return {"status": "invoked", "n8n_response": response.json()}
            except httpx.HTTPStatusError as e:
                logger.error(f"n8n invocation failed: {e}")
                return {"status": "error", "error": str(e)}

    async def cancel(self, execution_id: str) -> dict:
        # Implementation for cancelling an active n8n execution
        return {"status": "cancelled"}

    async def status(self, execution_id: str) -> dict:
        # Query n8n for workflow execution status
        return {"status": "unknown"}

    async def collect_result(self, execution_id: str) -> dict:
        # Collect final execution evidence and outputs
        return {"status": "collected"}

    async def health(self) -> dict:
        """Ping the n8n health endpoint."""
        url = f"{self.base_url}/healthz"
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    return {"status": "ok", "version": data.get("version", "unknown")}
                return {"status": "degraded"}
            except Exception as e:
                return {"status": "unavailable", "error": str(e)}
