import uuid
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from cappo_backend.security.mcp_gateway import MCPGateway

router = APIRouter(prefix="/mcp", tags=["MCP Bridge"])
gateway = MCPGateway()


class McpSecurity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nonce: str = Field(min_length=16, max_length=256)
    upstream_evidence_hash: str | None = Field(default=None, max_length=256)


class McpToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=64)
    security: McpSecurity

    @field_validator("arguments")
    @classmethod
    def bound_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value).encode("utf-8")) > 64 * 1024:
            raise ValueError("MCP arguments exceed the 64 KiB limit")
        return value


class McpCallResponse(BaseModel):
    status: Literal["blocked"]
    state: Literal["dependency_unavailable"]
    reason: str
    connection_id: str
    gateway_evidence_hash: str | None = None


@router.post("/call", response_model=McpCallResponse, status_code=503)
async def mcp_bridge_call(
    request: McpToolRequest,
    x_agent_id: str = Header(..., min_length=1, max_length=128, description="Veklom Agent ID"),
    x_capability_id: str = Header(
        ..., min_length=1, max_length=128, description="Veklom Capability ID"
    ),
):
    """Authorize an MCP call, then fail closed if no MCP transport is configured."""
    connection_id = str(uuid.uuid4())
    gateway_request = {
        "connection_id": connection_id,
        "agent_id": x_agent_id,
        "capability_id": x_capability_id,
        "nonce": request.security.nonce,
        "payload": {"action": f"mcp:{request.tool_name}", "data": request.arguments},
        "upstream_evidence_hash": request.security.upstream_evidence_hash,
    }

    gateway_result = await gateway.process_request(gateway_request)
    if "error" in gateway_result:
        raise HTTPException(
            status_code=int(gateway_result.get("error", {}).get("code", 500)),
            detail=f"cAPI Gatekeeper Reject: {gateway_result['error'].get('message')}",
        )
    if gateway_result.get("status") == "approval_required":
        raise HTTPException(
            status_code=403,
            detail=f"Approval Required: {gateway_result.get('approval_quorum')}",
        )

    # Governance authorization is not tool execution. Never report fabricated success.
    return McpCallResponse(
        status="blocked",
        state="dependency_unavailable",
        reason="MCP tool transport is not configured; execution was not attempted.",
        connection_id=connection_id,
        gateway_evidence_hash=gateway_result.get("evidence_hash"),
    )
