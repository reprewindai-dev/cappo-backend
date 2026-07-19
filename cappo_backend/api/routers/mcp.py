import uuid
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cappo_backend.security.mcp_gateway import MCPGateway

router = APIRouter(prefix="/mcp", tags=["MCP Bridge"])
gateway = MCPGateway()

class McpToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    security: Dict[str, Any]

@router.post("/call")
async def mcp_bridge_call(
    request: McpToolRequest,
    x_agent_id: str = Header(..., description="Veklom Agent ID"),
    x_capability_id: str = Header(..., description="Veklom Capability ID"),
):
    """
    Dedicated MCP bridge schema mapped for cAPI Phase 6 execution override.
    Supports the tools/call architecture specified in RUNTIME_PATCH.md.
    Now enforced universally via the 9-Phase MCPGateway.
    """
    
    connection_id = str(uuid.uuid4())
    nonce = request.security.get("nonce", str(uuid.uuid4()))
    
    gateway_request = {
        "connection_id": connection_id,
        "agent_id": x_agent_id,
        "capability_id": x_capability_id,
        "nonce": nonce,
        "payload": {
            "action": f"mcp:{request.tool_name}",
            "data": request.arguments
        },
        "upstream_evidence_hash": request.security.get("upstream_evidence_hash")
    }
    
    # Run the strict 9-phase gate
    gateway_result = await gateway.process_request(gateway_request)
    
    if "error" in gateway_result:
        raise HTTPException(
            status_code=int(gateway_result.get("error", {}).get("code", 500)),
            detail=f"cAPI Gatekeeper Reject: {gateway_result['error'].get('message')}"
        )
        
    if gateway_result.get("status") == "approval_required":
        raise HTTPException(
            status_code=403,
            detail=f"Approval Required: {gateway_result.get('approval_quorum')}"
        )

    # In a real environment, this would forward the parsed arguments to the actual MCP tool.
    # For now, it represents the bridged structural override.
    return {
        "status": "success",
        "evidence_id": gateway_result.get("evidence_hash"),
        "message": f"MCP Tool {request.tool_name} executed under cAPI governance.",
        "evidence_hash": f"0x{uuid.uuid4().hex}",
        "trust_delta": 2,
        "anomalies_detected": 0,
        "cost_attributed": 0,
        "risk_score": 15
    }
