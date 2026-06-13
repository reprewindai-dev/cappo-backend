"""PGL Adapter — bridges CAPPO orchestrator with real Veklom BYOS backend.

Provides a unified interface that lets the orchestrator work with either:
- Local database PGL (original PGLClient)
- Real Veklom BYOS backend (VeklomPGLClient)

This is the integration layer between CAPPO and the real agent registry.
"""

from __future__ import annotations

from typing import Any, Protocol

from cappo_backend.config import Settings, get_settings
from cappo_backend.services.pgl_client import PGLCertificate, PGLClient
from cappo_backend.services.veklom_pgl_client import VeklomAgentCertificate, VeklomPGLClient


class PGLPort(Protocol):
    """Unified interface for PGL operations.
    
    Both PGLClient (local DB) and VeklomPGLClient (external API) implement
    this interface through the adapter.
    """
    
    def get_certificate(self, certificate_id: str) -> Any | None: ...
    def mint_pre_certificate(self, **kwargs: Any) -> Any: ...
    def mint_post_certificate(self, **kwargs: Any) -> Any: ...


class VeklomPGLAdapter:
    """Adapter that makes VeklomPGLClient work with CAPPO orchestrator.
    
    Translates between:
    - CAPPO's certificate_id lookups → veklom agent_id lookups
    - CAPPO's local PGL minting → veklom agent validation
    - CAPPO's risk_tier → veklom trust_score
    
    Usage:
        adapter = VeklomPGLAdapter(db_session)
        orchestrator = RunOrchestrator(
            db=db,
            pgl=adapter,  # Acts like PGLClient
            ...
        )
    """
    
    def __init__(self, db: Any | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._veklom = VeklomPGLClient(settings)
        self._local = PGLClient(db=db, settings=settings) if db else None
        self._cert_cache: dict[str, VeklomAgentCertificate] = {}
    
    @property
    def persistent(self) -> bool:
        """Always true when connected to real veklom backend."""
        return True
    
    def get_certificate(self, certificate_id: str) -> PGLCertificate | None:
        """Lookup certificate by ID.
        
        For veklom: certificate_id is agent_id.
        Fetches from veklom API and converts to PGLCertificate format.
        """
        # Check cache first
        if certificate_id in self._cert_cache:
            agent_cert = self._cert_cache[certificate_id]
            return self._to_pgl_certificate(agent_cert)
        
        try:
            # Try veklom (agent_id lookup)
            agent_cert = self._veklom.get_agent_certificate(certificate_id)
            self._cert_cache[certificate_id] = agent_cert
            return self._to_pgl_certificate(agent_cert)
        except Exception:
            # Fallback to local DB if available
            if self._local:
                return self._local.get_certificate(certificate_id)
            return None
    
    def _to_pgl_certificate(self, agent: VeklomAgentCertificate) -> PGLCertificate:
        """Convert VeklomAgentCertificate to PGLCertificate format."""
        cert = PGLCertificate(
            certificate_id=agent.agent_id,  # Use agent_id as cert_id
            run_id=agent.certificate_id,  # Use veklom cert_id as run_id
            workspace_id=agent.jurisdiction,  # Jurisdiction as workspace
            genome_hash=agent.genome_hash,
            constitution_hash=self._hash_constitution(agent),
            plan_hash=self._hash_plan(agent),
            governance_decision="ALLOW" if agent.is_active else "DENY",
            risk_tier=self._trust_score_to_risk_tier(agent.trust_score),
            approved_budget_cents=1000,  # Default budget
            persisted=True,
        )
        # Attach veklom data for reference
        cert._veklom_agent = agent  # type: ignore
        return cert
    
    def _hash_constitution(self, agent: VeklomAgentCertificate) -> str:
        """Generate constitution hash from agent rules."""
        import hashlib
        import json
        
        constitution = {
            "safety_rules": agent.safety_rules,
            "permissions": agent.permissions,
            "risk_category": agent.risk_category,
        }
        return hashlib.sha256(
            json.dumps(constitution, sort_keys=True).encode()
        ).hexdigest()
    
    def _hash_plan(self, agent: VeklomAgentCertificate) -> str:
        """Generate plan hash from declared purpose."""
        import hashlib
        
        return hashlib.sha256(
            agent.declared_purpose.encode()
        ).hexdigest()
    
    def _trust_score_to_risk_tier(self, trust_score: float) -> str:
        """Convert veklom trust score to CAPPO risk tier.
        
        90-100: production (high trust)
        70-89: standard (medium trust)
        40-69: sandbox (low trust)
        0-39: terminated (blocked)
        """
        if trust_score >= 90:
            return "production"
        elif trust_score >= 70:
            return "standard"
        elif trust_score >= 40:
            return "sandbox"
        else:
            return "terminated"
    
    def validate_agent(
        self,
        agent_id: str,
        requested_tools: list[str] | None = None,
        budget_cents: int = 0,
    ) -> VeklomAgentCertificate:
        """Full agent validation using real veklom backend."""
        return self._veklom.validate_agent_for_execution(
            agent_id=agent_id,
            requested_tools=requested_tools,
            budget_cents=budget_cents,
        )
    
    def get_trust_score(self, agent_id: str) -> float:
        """Get real trust score from veklom."""
        try:
            cert = self._veklom.get_agent_certificate(agent_id)
            return cert.trust_score
        except Exception:
            return 0.0
    
    def mint_pre_certificate(self, **kwargs: Any) -> PGLCertificate:
        """For veklom integration, this validates agent instead of minting.
        
        The real certificate already exists in veklom - we just validate it.
        """
        run_id = kwargs.get("run_id", "")
        workspace_id = kwargs.get("workspace_id", "")
        
        # Use run_id as agent_id to lookup
        try:
            agent = self._veklom.validate_agent_for_execution(
                agent_id=run_id,
                requested_tools=kwargs.get("tools", []),
                budget_cents=kwargs.get("approved_budget_cents", 0),
            )
            return self._to_pgl_certificate(agent)
        except Exception:
            # Fallback to local if veklom fails
            if self._local:
                return self._local.mint_pre_certificate(**kwargs)
            raise
    
    def mint_post_certificate(self, **kwargs: Any) -> PGLCertificate:
        """Record execution attestation back to veklom ledger.
        
        After CAPPO execution completes, this updates the agent's
        ledger with the execution outcome.
        """
        run_id = kwargs.get("run_id", "")
        outcome = kwargs.get("outcome", {})
        
        # Record in veklom
        try:
            execution_id = kwargs.get("execution_id", "")
            self._veklom.record_execution_attestation(
                agent_id=run_id,
                execution_id=execution_id,
                outcome=outcome,
            )
        except Exception:
            pass  # Non-fatal - CAPPO attestation is primary
        
        # Also mint local if available
        if self._local:
            return self._local.mint_post_certificate(**kwargs)
        
        # Return minimal cert
        return PGLCertificate(
            certificate_id=str(kwargs.get("pre_certificate_id", "")),
            run_id=run_id,
            workspace_id=kwargs.get("workspace_id", ""),
            genome_hash=kwargs.get("genome_hash", ""),
            constitution_hash="",
            plan_hash="",
            governance_decision="ALLOW",
            risk_tier="standard",
            persisted=True,
        )


def create_pgl_client(
    db: Any | None = None,
    settings: Settings | None = None,
    use_veklom: bool = True,
) -> PGLClient | VeklomPGLAdapter:
    """Factory to create appropriate PGL client.
    
    Args:
        db: Database session (for local fallback)
        settings: CAPPO settings
        use_veklom: If True and veklom URL configured, use real backend
        
    Returns:
        PGL client (veklom adapter or local DB client)
    """
    settings = settings or get_settings()
    
    if use_veklom and settings.veklom_byos_backend_url:
        return VeklomPGLAdapter(db=db, settings=settings)
    
    return PGLClient(db=db, settings=settings)
