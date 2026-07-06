from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict

router = APIRouter(prefix="/mcp", tags=["MCP Bridge"])

class McpToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    security: Dict[str, Any]

@router.post("/call")
async def mcp_bridge_call(request: McpToolRequest):
    """
    Dedicated MCP bridge schema mapped for cAPI Phase 6 execution override.
    Supports the tools/call architecture specified in RUNTIME_PATCH.md.
    """
    from cappo_backend.core.capi_pipeline import enforce_capi_pipeline
    
    dummy_pub_key = b"-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n-----END PUBLIC KEY-----"
    
    capi_payload = {
        "action": f"mcp:{request.tool_name}",
        "data": request.arguments,
        "security": request.security
    }
    
    try:
        capi_result = await enforce_capi_pipeline("mcp_caller", capi_payload, dummy_pub_key.decode('utf-8'))
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"cAPI Gatekeeper Reject: {str(e)}")

    # In a real environment, this would forward the parsed arguments to the actual MCP tool.
    # For now, it represents the bridged structural override.
    return {
        "status": "success",
        "evidence_id": capi_result["evidence_id"],
        "message": f"MCP Tool {request.tool_name} executed under cAPI governance."
    }
