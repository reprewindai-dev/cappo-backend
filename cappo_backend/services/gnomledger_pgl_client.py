import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from cappo_backend.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class GnomledgerAgentCertificate:
    """Canonical Gnomledger agent certificate representation."""

    agent_id: str
    certificate_id: str
    name: str
    creator: str
    jurisdiction: str
    declared_purpose: str
    status: str
    trust_score: float
    risk_tier: str
    evidence_head: str | None
    genome_hash: str
    safety_rules: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    risk_category: str = "medium"
    is_active: bool = True
    parent_agent_ids: list[str] = field(default_factory=list)


class GnomledgerPGLClient:
    """Client for interacting with the canonical Gnomledger REST API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.gnomledger_url
        if not self.base_url:
            raise ValueError("gnomledger_url is not configured")
            
        self.api_key = self.settings.gnomledger_api_key
        
        # Remove trailing slash if present
        if self.base_url.endswith("/"):
            self.base_url = self.base_url[:-1]

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def get_agent_certificate(self, agent_id: str) -> GnomledgerAgentCertificate:
        """Fetch agent certificate details from Gnomledger."""
        url = f"{self.base_url}/api/v1/agents/{agent_id}"
        
        try:
            with httpx.Client() as client:
                response = client.get(url, headers=self._get_headers(), timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                genome = data.get("genome", {})
                
                return GnomledgerAgentCertificate(
                    agent_id=data["agent_id"],
                    certificate_id=data["certificate_id"],
                    name=data["name"],
                    creator=data["creator"],
                    jurisdiction=data["jurisdiction"],
                    declared_purpose=data["declared_purpose"],
                    status=data["status"],
                    trust_score=float(data.get("trust_score", 0.0)),
                    risk_tier=data.get("risk_tier", "terminated"),
                    evidence_head=data.get("evidence_head"),
                    genome_hash=data.get("latest_genome_hash", ""),
                    safety_rules=genome.get("safety_rules", []),
                    permissions=genome.get("permissions", []),
                    risk_category=genome.get("risk_category", "medium"),
                    is_active=data.get("status", "").lower() == "active",
                    parent_agent_ids=data.get("parent_agent_ids", []),
                )
        except httpx.HTTPError as e:
            logger.error("HTTP error fetching agent certificate %s from Gnomledger: %s", agent_id, e)
            raise

    def validate_agent_for_execution(
        self,
        agent_id: str,
    ) -> GnomledgerAgentCertificate:
        """Validates agent is active and not terminated."""
        cert = self.get_agent_certificate(agent_id)
        
        if not cert.is_active:
            raise ValueError(f"Agent {agent_id} is not active (status: {cert.status})")
            
        if cert.risk_tier == "terminated" or cert.trust_score <= 40:
            raise ValueError(f"Agent {agent_id} trust score {cert.trust_score} is below termination threshold")
            
        return cert

    def record_execution_attestation(
        self,
        agent_id: str,
        event_type: str,
        summary: str,
        details: dict[str, Any],
    ) -> str:
        """
        Record a pre/post execution attestation to Gnomledger.
        Returns the ledger event ID.
        """
        url = f"{self.base_url}/api/v1/ledger/events"
        
        payload = {
            "agent_id": agent_id,
            "event_type": event_type,
            "actor": "cappo-backend",
            "summary": summary,
            "details": details,
        }
        
        try:
            with httpx.Client() as client:
                response = client.post(url, headers=self._get_headers(), json=payload, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                return data.get("event_id", "")
        except httpx.HTTPError as e:
            logger.error("HTTP error recording execution attestation to Gnomledger for %s: %s", agent_id, e)
            raise
