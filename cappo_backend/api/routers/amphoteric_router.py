"""
Amphoteric Router for CAPPO — Unified Sovereign Execution.
"""

from typing import Any, Dict
from fastapi import APIRouter, Depends, Request
from cappo_backend.security.amphoteric_middleware import AmphotericProtocol

router = APIRouter(prefix="/v1/amphoteric", tags=["Amphoteric"])

@router.get("/discover")
async def discover_capabilities(request: Request):
    protocol = getattr(request.state, "amphoteric_protocol", AmphotericProtocol.REST_API)

    # In CAPPO, discovery returns governed tools/actions
    capabilities = [
        {"name": "governed_exec", "description": "Execute AI inference via RunOrchestrator"},
        {"name": "audit_query", "description": "Query the hash-chained audit ledger"},
        {"name": "doc_discover", "description": "Vector-based discovery of documents across a workspace"},
        {"name": "doc_reason", "description": "Deep reasoning and evidence extraction from a specific long document"},
        {"name": "doc_trace", "description": "Retrieve provenance metadata for a document reasoning session"}
    ]

    if protocol == AmphotericProtocol.WEBMCP:
        return {
            "protocol": "WebMCP/1.0",
            "tools": capabilities
        }
    elif protocol == AmphotericProtocol.MCP_RPC:
        return {
            "jsonrpc": "2.0",
            "result": {"tools": capabilities}
        }
    else:
        return {"capabilities": capabilities}
