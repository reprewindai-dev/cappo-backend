"""Veklom BYOS PGL Client — connects to real veklom-byos-backend API.

Replaces local database PGL with external Veklom service.
Fetches real agent certificates, trust scores, and lineage from the
production veklom agent registry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from cappo_backend.config import Settings, get_settings


class VeklomPGLError(Exception):
    """Raised when veklom-byos-backend API call fails."""


class AgentNotFoundError(VeklomPGLError):
    """Raised when agent_id is not found in veklom registry."""


class CertificateInvalidError(VeklomPGLError):
    """Raised when agent certificate is revoked or invalid."""


@dataclass
class VeklomAgentCertificate:
    """Real agent certificate from veklom-byos-backend."""
    agent_id: str
    certificate_id: str
    name: str
    creator: str
    jurisdiction: str
    declared_purpose: str
    status: str  # active, revoked, suspended
    genome_hash: str
    model_family: str
    model_version: str
    tools: list[str]
    permissions: list[str]
    safety_rules: list[str]
    risk_category: str  # high, medium, low
    trust_score: float  # 0-100
    parent_agent_ids: list[str]
    created_at: str
    version_count: int
    ledger_events: list[dict[str, Any]]
    
    @property
    def is_active(self) -> bool:
        return self.status == "active"
    
    @property
    def is_revoked(self) -> bool:
        return self.status == "revoked"


class VeklomPGLClient:
    """Client for veklom-byos-backend PGL API.
    
    Fetches real agent certificates and validates trust scores
    from the production veklom agent registry.
    """
    
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base_url = self._settings.veklom_byos_backend_url or os.getenv("VEKLOM_BYOS_BACKEND_URL", "")
        self._api_key = self._settings.veklom_api_key or os.getenv("VEKLOM_API_KEY", "")
        
        if not self._base_url:
            raise VeklomPGLError("VEKLOM_BYOS_BACKEND_URL not configured")

        self._client = httpx.Client(timeout=30)
    
    def _request(self, method: str, path: str, **kwargs) -> Any:
        """Make authenticated request to veklom-byos-backend."""
        url = f"{self._base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = kwargs.pop("headers", {})
        
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        
        try:
            response = self._client.request(
                method=method,
                url=url,
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise AgentNotFoundError(f"Agent not found: {path}")
            raise VeklomPGLError(f"Veklom API error: {e}")
        except httpx.RequestError as e:
            raise VeklomPGLError(f"Veklom API request failed: {e}")
    
    def get_agent_certificate(self, agent_id: str) -> VeklomAgentCertificate:
        """Fetch agent certificate from veklom-byos-backend.
        
        Returns real PGL certificate with trust score, genome hash,
        and full lineage history.
        """
        data = self._request("GET", f"/pgl/agents/{agent_id}/certificate")
        
        agent = data.get("agent", data)  # Handle different response shapes
        
        return VeklomAgentCertificate(
            agent_id=agent.get("agent_id", agent_id),
            certificate_id=agent.get("certificate_id", ""),
            name=agent.get("name", ""),
            creator=agent.get("creator", ""),
            jurisdiction=agent.get("jurisdiction", ""),
            declared_purpose=agent.get("declared_purpose", ""),
            status=agent.get("status", "unknown"),
            genome_hash=agent.get("latest_genome_hash", ""),
            model_family=agent.get("genome", {}).get("model_family", ""),
            model_version=agent.get("genome", {}).get("model_version", ""),
            tools=agent.get("genome", {}).get("tools", []),
            permissions=agent.get("genome", {}).get("permissions", []),
            safety_rules=agent.get("genome", {}).get("safety_rules", []),
            risk_category=agent.get("genome", {}).get("risk_category", "medium"),
            trust_score=self._calculate_trust_score(agent),
            parent_agent_ids=agent.get("parent_agent_ids", []),
            created_at=agent.get("created_at", ""),
            version_count=agent.get("version_count", 1),
            ledger_events=agent.get("ledger_events", []),
        )
    
    def _calculate_trust_score(self, agent: dict[str, Any]) -> float:
        """Calculate trust score from agent governance data.
        
        Uses veklom's ledger events to compute real trust score:
        - Birth registration: base 50
        - Deployments: +10 each
        - Test audits: +score/10 each
        - Violations: -20 each
        """
        base_score = 50.0
        ledger = agent.get("ledger_events", [])
        
        for event in ledger:
            event_type = event.get("event_type", "")
            details = event.get("details", {})
            
            if event_type == "birth_registration":
                base_score = max(base_score, 50)  # Floor at 50
            elif event_type == "deployment":
                base_score += 10
            elif event_type == "test_audit":
                score = details.get("score", 0)
                base_score += score / 10
            elif event_type == "violation":
                base_score -= 20
        
        # Clamp to 0-100
        return max(0.0, min(100.0, base_score))
    
    def validate_agent_for_execution(
        self,
        agent_id: str,
        requested_tools: list[str] | None = None,
        budget_cents: int = 0,
    ) -> VeklomAgentCertificate:
        """Full validation before CAPPO execution.
        
        Validates:
        1. Agent exists in registry
        2. Agent is active (not revoked/suspended)
        3. Requested tools are in agent's allowed tools
        4. Trust score > 40 (termination threshold)
        5. Safety rules compliance
        
        Returns:
            Validated certificate ready for EI minting
            
        Raises:
            AgentNotFoundError: Agent doesn't exist
            CertificateInvalidError: Agent revoked, tools not allowed, or score <= 40
        """
        cert = self.get_agent_certificate(agent_id)
        
        # Check agent status
        if cert.is_revoked:
            raise CertificateInvalidError(
                f"Agent {agent_id} has been revoked. Terminated."
            )
        
        if not cert.is_active:
            raise CertificateInvalidError(
                f"Agent {agent_id} is not active (status: {cert.status})"
            )
        
        # Check trust score threshold (40 = termination)
        if cert.trust_score <= 40:
            raise CertificateInvalidError(
                f"Agent {agent_id} trust score {cert.trust_score:.1f} below termination threshold (40). "
                "Agent is terminated."
            )
        
        # Check tool permissions
        if requested_tools:
            allowed_tools = set(cert.tools)
            for tool in requested_tools:
                if tool not in allowed_tools:
                    raise CertificateInvalidError(
                        f"Agent {agent_id} not authorized for tool: {tool}. "
                        f"Allowed: {cert.tools}"
                    )
        
        # Check safety rules (human escalation for high risk)
        if cert.risk_category == "high" and "human_escalation" not in cert.safety_rules:
            raise CertificateInvalidError(
                f"High-risk agent {agent_id} missing human_escalation safety rule"
            )
        
        return cert
    
    def get_agent_lineage(self, agent_id: str) -> dict[str, Any]:
        """Fetch agent lineage (parent/children) from veklom."""
        return self._request("GET", f"/pgl/agents/{agent_id}/lineage")
    
    def get_agent_ledger(self, agent_id: str) -> list[dict[str, Any]]:
        """Fetch full ledger history for agent."""
        data = self._request("GET", f"/pgl/agents/{agent_id}/ledger")
        return data.get("ledger_events", [])
    
    def record_execution_attestation(
        self,
        agent_id: str,
        execution_id: str,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        """Record execution outcome back to veklom ledger.
        
        This creates a new ledger event linking the CAPPO execution
        to the agent's permanent history.
        """
        return self._request(
            "POST",
            f"/pgl/agents/{agent_id}/ledger",
            json={
                "event_type": "execution_attestation",
                "actor": "CAPPO",
                "summary": f"Execution {execution_id} completed",
                "details": {
                    "execution_id": execution_id,
                    "outcome": outcome,
                    "source": "cappo-backend",
                },
            },
        )


# Factory function for dependency injection
def get_veklom_pgl_client() -> VeklomPGLClient:
    """Get configured veklom PGL client."""
    return VeklomPGLClient()
