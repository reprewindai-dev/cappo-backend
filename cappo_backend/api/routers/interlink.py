import json
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway

router = APIRouter()
runtime = MCPGateway()

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_interlink(
    path: str,
    request: Request,
    x_agent_id: str = Header(..., description="Veklom Agent ID"),
    x_capability_id: str = Header(..., description="Veklom Capability ID"),
    x_target_url: str = Header(..., description="The external Web2 API URL to forward to"),
    x_execution_identity: str = Header(..., description="JSON String of the CAPPO Execution Identity"),
    x_provider_key: Optional[str] = Header(None, description="BYOK external API key to forward as Authorization"),
):
    """
    Interlink Proxy Endpoint.
    Intercepts the request, enforces VNP micro-stakes via the local ledger, 
    verifies Law 0 Execution Identity, and forwards the raw request to the 
    legacy Web2 API without X-Veklom headers.
    """
    
    try:
        execution_identity = json.loads(x_execution_identity)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid X-Execution-Identity JSON format")
    
    # Extract original headers, stripping out Veklom-specific proprietary headers
    outbound_headers = {}
    for key, value in request.headers.items():
        if key.lower() not in [
            "host", "x-agent-id", "x-capability-id", "x-target-url", 
            "x-veklom-receipt-id", "x-execution-identity", "x-provider-key"
        ]:
            outbound_headers[key] = value
            
    # Inject BYOK API key into standard Authorization header
    if x_provider_key:
        outbound_headers["Authorization"] = f"Bearer {x_provider_key}"

    payload = {
        "target_url": x_target_url,
        "path": path,
        "method": request.method
    }

    # 1. Enforce Veklom Ledger & Budget & Law 0 BEFORE forwarding (Zero-Trust Interlink)
    try:
        auth_result = await runtime.process_interlink_request(
            agent_id=x_agent_id,
            capability_id=x_capability_id,
            payload=payload,
            execution_identity=execution_identity,
            estimated_cost=1.5 # Fixed micro-stake for proxy execution
        )
    except EIValidationError as e:
        raise HTTPException(status_code=403, detail={"law0": True, "error": str(e)})

    if auth_result.get("status") != "authorized":
        raise HTTPException(status_code=402, detail=auth_result)

    # 2. Forward the request to the external un-governed API
    body = await request.body()
    
    # Ensure target URL ends properly
    if x_target_url.endswith("/"):
        x_target_url = x_target_url[:-1]
    
    full_target_url = f"{x_target_url}/{path}"
    if request.query_params:
        full_target_url = f"{full_target_url}?{request.query_params}"

    try:
        async with httpx.AsyncClient() as client:
            proxy_req = client.build_request(
                method=request.method,
                url=full_target_url,
                headers=outbound_headers,
                content=body
            )
            proxy_resp = await client.send(proxy_req)
            
            # 3. Return transparently to the Agent, attaching the cryptographic evidence hash
            headers = dict(proxy_resp.headers)
            headers["X-Veklom-Receipt-ID"] = auth_result.get("evidence_hash", "")
            
            return JSONResponse(
                status_code=proxy_resp.status_code,
                content=proxy_resp.json() if proxy_resp.headers.get("content-type") == "application/json" else proxy_resp.text,
                headers=headers
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Bad Gateway: External API failed - {str(e)}")
