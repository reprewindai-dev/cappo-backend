import json
from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway

router = APIRouter()
runtime = MCPGateway()
MAX_PROXY_BODY_BYTES = 1_048_576
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
}

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_interlink(
    path: str,
    request: Request,
    x_agent_id: str = Header(..., min_length=1, max_length=128, description="Veklom Agent ID"),
    x_capability_id: str = Header(..., min_length=1, max_length=128, description="Veklom Capability ID"),
    x_target_url: str = Header(..., min_length=1, max_length=2048, description="The external Web2 API URL to forward to"),
    x_execution_identity: str = Header(..., min_length=2, max_length=16384, description="JSON String of the CAPPO Execution Identity"),
    x_provider_key: Optional[str] = Header(None, max_length=4096, description="BYOK external API key to forward as Authorization"),
):
    """
    Interlink Proxy Endpoint.
    Intercepts the request, enforces VNP micro-stakes via the local ledger, 
    verifies Law 0 Execution Identity, and forwards the raw request to the 
    legacy Web2 API without X-Veklom headers.
    """
    
    parsed_target = urlsplit(x_target_url)
    if parsed_target.scheme not in {"http", "https"} or not parsed_target.hostname:
        raise HTTPException(status_code=400, detail="X-Target-URL must be an absolute HTTP(S) URL")
    if parsed_target.username or parsed_target.password:
        raise HTTPException(status_code=400, detail="X-Target-URL must not contain credentials")
    if len(path) > 2048:
        raise HTTPException(status_code=400, detail="Proxy path exceeds the 2048 character limit")

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
    if len(body) > MAX_PROXY_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Proxy request body exceeds 1 MiB limit")
    
    # Ensure target URL ends properly
    if x_target_url.endswith("/"):
        x_target_url = x_target_url[:-1]
    
    full_target_url = f"{x_target_url}/{path}"
    if request.query_params:
        full_target_url = f"{full_target_url}?{request.query_params}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
            proxy_req = client.build_request(
                method=request.method,
                url=full_target_url,
                headers=outbound_headers,
                content=body
            )
            proxy_resp = await client.send(proxy_req)
            
            # 3. Return transparently to the Agent, attaching the cryptographic evidence hash
            headers = {
                key: value for key, value in proxy_resp.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            headers["X-Veklom-Receipt-ID"] = auth_result.get("evidence_hash", "")
            headers["X-Veklom-Proxy-State"] = (
                "success" if 200 <= proxy_resp.status_code < 300 else "downstream_failure"
            )

            return Response(content=proxy_resp.content, status_code=proxy_resp.status_code, headers=headers)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Bad Gateway: external dependency failed")
