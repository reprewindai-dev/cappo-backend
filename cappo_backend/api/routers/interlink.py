import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Optional

from cappo_backend.security.mcp_gateway import EnhancedMCPAPIRuntime

router = APIRouter()
runtime = EnhancedMCPAPIRuntime()

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_interlink(
    path: str,
    request: Request,
    x_agent_id: str = Header(..., description="Veklom Agent ID"),
    x_capability_id: str = Header(..., description="Veklom Capability ID"),
    x_target_url: str = Header(..., description="The external Web2 API URL to forward to"),
):
    """
    Interlink Proxy Endpoint.
    Intercepts the request, enforces VNP micro-stakes via the local ledger, 
    and forwards the raw request to the legacy Web2 API without X-Veklom headers.
    """
    
    # Extract original headers, stripping out Veklom-specific proprietary headers
    outbound_headers = {}
    for key, value in request.headers.items():
        if key.lower() not in ["host", "x-agent-id", "x-capability-id", "x-target-url", "x-veklom-receipt-id"]:
            outbound_headers[key] = value

    payload = {
        "target_url": x_target_url,
        "path": path,
        "method": request.method
    }

    # 1. Enforce Veklom Ledger & Budget BEFORE forwarding (Zero-Trust Interlink)
    auth_result = await runtime.process_interlink_request(
        agent_id=x_agent_id,
        capability_id=x_capability_id,
        payload=payload,
        estimated_cost=1.5 # Fixed micro-stake for proxy execution
    )

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
