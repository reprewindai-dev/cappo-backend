"""PGL Adapter — bridges CAPPO orchestrator with real Veklom BYOS backend.

Provides a unified interface that lets the orchestrator work with either:
- Local database PGL (original PGLClient)
- Real Veklom BYOS backend (VeklomPGLClient)

This is the integration layer between CAPPO and the real agent registry.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from cappo_backend.config import Settings, get_settings
from cappo_backend.services.gnomledger_pgl_client import (
    GnomledgerAgentCertificate,
    GnomledgerPGLClient,
)
from cappo_backend.services.pgl_client import (
    PGLCertificate,
    PGLClient,
    PostCertificateParams,
    PreCertificateParams,
)
from cappo_backend.services.veklom_pgl_client import VeklomAgentCertificate, VeklomPGLClient

logger = logging.getLogger(__name__)


class PGLPort(Protocol):
    """Unified interface for PGL operations.

    Both PGLClient (local DB) and VeklomPGLClient (external API) implement
    this interface through the adapter.
    """

    def get_certificate(self, certificate_id: str) -> Any | None: ...
    def mint_pre_certificate(self, params: PreCertificateParams) -> Any: ...
    def mint_post_certificate(self, params: PostCertificateParams) -> Any: ...
    def append_evidence_event(
        self,
        *,
        certificate_id: str,
        event_type: str,
        evidence: dict[str, Any],
        agent_id: str | None = None,
    ) -> Any: ...


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
        except Exception as e:
            logger.warning(
                "Failed to get agent certificate from Veklom for %s: %s", certificate_id, e
            )
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
        return hashlib.sha256(json.dumps(constitution, sort_keys=True).encode()).hexdigest()

    def _hash_plan(self, agent: VeklomAgentCertificate) -> str:
        """Generate plan hash from declared purpose."""
        import hashlib

        return hashlib.sha256(agent.declared_purpose.encode()).hexdigest()

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
        except Exception as e:
            logger.warning("Failed to get trust score from Veklom for %s: %s", agent_id, e)
            return 0.0
    
    def mint_pre_certificate(self, params: PreCertificateParams) -> PGLCertificate:
        """For veklom integration, this validates agent instead of minting.

        The real certificate already exists in veklom - we just validate it.
        """
        run_id = params.run_id
        
        # Use run_id as agent_id to lookup
        try:
            # Note: `tools` is not part of PreCertificateParams directly, so we assume empty or we need to pass it differently.
            # In cappo_backend/services/orchestrator.py, `mint_pre_certificate` doesn't currently pass `tools`.
            # We'll pass an empty list here, which matches the default behavior of `kwargs.get("tools", [])`.
            agent = self._veklom.validate_agent_for_execution(
                agent_id=run_id,
                requested_tools=[],
                budget_cents=params.approved_budget_cents,
            )
            return self._to_pgl_certificate(agent)
        except Exception as e:
            logger.warning("Failed to validate agent for execution in Veklom for %s: %s", run_id, e)
            # Fallback to local if veklom fails
            if self._local:
                return self._local.mint_pre_certificate(params)
            raise
    
    def mint_post_certificate(self, params: PostCertificateParams) -> PGLCertificate:
        """Record execution attestation back to veklom ledger.

        After CAPPO execution completes, this updates the agent's
        ledger with the execution outcome.
        """
        run_id = params.run_id
        # We don't have outcome directly, but we can synthesize it from outcome_hash or execution_id
        # Note: In cappo_backend/services/orchestrator.py, `mint_post_certificate` doesn't currently pass `outcome` or `execution_id`.
        # We'll use defaults as before.
        outcome = {}
        execution_id = ""
        

        # Record in veklom
        try:
            self._veklom.record_execution_attestation(
                agent_id=run_id,
                execution_id=execution_id,
                outcome=outcome,
            )
        except Exception as e:
            logger.warning("Failed to record execution attestation to Veklom for %s: %s", run_id, e)
            # Non-fatal - CAPPO attestation is primary

        # Also mint local if available
        if self._local:
            return self._local.mint_post_certificate(params)
        
        # Return minimal cert
        return PGLCertificate(
            certificate_id=str(params.pre_certificate_id),
            run_id=run_id,
            workspace_id=params.workspace_id,
            genome_hash=params.genome_hash,
            constitution_hash="",
            plan_hash="",
            governance_decision="ALLOW",
            risk_tier="standard",
            persisted=True,
        )

    def append_evidence_event(
        self,
        *,
        certificate_id: str,
        event_type: str,
        evidence: dict[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        if not agent_id:
            raise ValueError("agent_id is required to seal evidence in the BYOS PGL")
        response = self._veklom.record_execution_attestation(
            agent_id=agent_id,
            execution_id=certificate_id,
            outcome={"event_type": event_type, "evidence_seal": evidence},
        )
        if not isinstance(response, dict) or not response.get("event_id"):
            raise RuntimeError("BYOS PGL did not acknowledge the evidence event")
        return response


class GnomledgerPGLAdapter:
    """Adapter that makes GnomledgerPGLClient work with CAPPO orchestrator.

    This is the canonical path — gnomledger is the dedicated, hash-chained
    Project Genome Ledger. Prefer this adapter over VeklomPGLAdapter.
    """

    def __init__(self, db: Any | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._gnomledger = GnomledgerPGLClient(settings)
        self._local = PGLClient(db=db, settings=settings) if db else None
        self._cert_cache: dict[str, GnomledgerAgentCertificate] = {}

    @property
    def persistent(self) -> bool:
        """True only if Gnomledger is configured and ready."""
        return bool(self._settings.gnomledger_url)

    def get_certificate(self, certificate_id: str) -> PGLCertificate | None:
        if certificate_id in self._cert_cache:
            return self._to_pgl_certificate(self._cert_cache[certificate_id])
        try:
            agent_cert = self._gnomledger.get_agent_certificate(certificate_id)
            self._cert_cache[certificate_id] = agent_cert
            return self._to_pgl_certificate(agent_cert)
        except Exception as e:
            logger.warning("Failed to get agent certificate from gnomledger for %s: %s", certificate_id, e)
            if self._local and self._settings.cappo_allow_noncanonical_pgl_fallback:
                return self._local.get_certificate(certificate_id)
            return None

    def _to_pgl_certificate(self, agent: GnomledgerAgentCertificate) -> PGLCertificate:
        cert = PGLCertificate(
            certificate_id=agent.agent_id,
            run_id=agent.certificate_id,
            workspace_id=agent.jurisdiction,
            genome_hash=agent.genome_hash,
            constitution_hash=self._hash_constitution(agent),
            plan_hash=self._hash_plan(agent),
            governance_decision="ALLOW" if agent.is_active else "DENY",
            risk_tier=agent.risk_tier,
            approved_budget_cents=0,  # Execution budget shouldn't be read from identity
            persisted=True,
        )
        cert._gnomledger_agent = agent  # type: ignore
        return cert

    def _hash_constitution(self, agent: GnomledgerAgentCertificate) -> str:
        import hashlib
        import json

        constitution = {
            "safety_rules": agent.safety_rules,
            "permissions": agent.permissions,
            "risk_category": agent.risk_category,
        }
        return hashlib.sha256(json.dumps(constitution, sort_keys=True).encode()).hexdigest()

    def _hash_plan(self, agent: GnomledgerAgentCertificate) -> str:
        import hashlib
        return hashlib.sha256(agent.declared_purpose.encode()).hexdigest()

    def validate_agent(
        self,
        agent_id: str,
        requested_tools: list[str] | None = None,
        budget_cents: int = 0,
    ) -> GnomledgerAgentCertificate:
        return self._gnomledger.validate_agent_for_execution(agent_id=agent_id)

    def get_trust_score(self, agent_id: str) -> float:
        try:
            return self._gnomledger.get_agent_certificate(agent_id).trust_score
        except Exception as e:
            logger.warning("Failed to get trust score from gnomledger for %s: %s", agent_id, e)
            return 0.0

    def mint_pre_certificate(self, params: PreCertificateParams) -> PGLCertificate:
        agent_id = params.agent_id or params.run_id
        try:
            # 1. Resolve identity
            agent = self._gnomledger.validate_agent_for_execution(agent_id=agent_id)
            
            # 2. Append pre_execution_authorization event
            event_id = self._gnomledger.record_execution_attestation(
                agent_id=agent.agent_id,
                event_type="pre_execution_authorization",
                summary=f"Execution authorization for run {params.run_id}",
                details={
                    "run_id": params.run_id,
                    "genome_hash": params.genome_hash,
                    "input_hash": params.input_hash,
                    "decision_frame_hash": params.decision_frame_hash,
                    "approved_budget_cents": params.approved_budget_cents,
                    "reserve_cents": params.reserve_cents,
                }
            )
            
            # 3. Return execution certificate
            cert = self._to_pgl_certificate(agent)
            cert.certificate_id = event_id  # Unique CAPPO execution certificate ID
            cert.run_id = params.run_id
            cert.approved_budget_cents = params.approved_budget_cents
            return cert
        except Exception as e:
            logger.warning("Failed to authorize execution in gnomledger for %s: %s", agent_id, e)
            if self._local and self._settings.cappo_allow_noncanonical_pgl_fallback:
                return self._local.mint_pre_certificate(params)
            raise

    def mint_post_certificate(self, params: PostCertificateParams) -> PGLCertificate:
        try:
            self._gnomledger.record_execution_attestation(
                agent_id=params.agent_id or params.run_id,
                event_type="post_execution_attestation",
                summary=f"Execution attestation for run {params.run_id}",
                details={
                    "run_id": params.run_id,
                    "execution_id": params.pre_certificate_id,
                    "outcome_hash": params.outcome_hash,
                    "output_hash": params.output_hash,
                }
            )
        except Exception as e:
            logger.warning("Failed to record execution attestation to gnomledger for %s: %s", params.run_id, e)
            if not (self._local and self._settings.cappo_allow_noncanonical_pgl_fallback):
                raise

        if self._local and self._settings.cappo_allow_noncanonical_pgl_fallback:
            return self._local.mint_post_certificate(params)

        return PGLCertificate(
            certificate_id=str(params.pre_certificate_id),
            run_id=params.run_id,
            workspace_id=params.workspace_id,
            genome_hash=params.genome_hash,
            constitution_hash="",
            plan_hash="",
            governance_decision="ALLOW",
            risk_tier="standard",
            persisted=True,
        )

    def append_evidence_event(
        self,
        *,
        certificate_id: str,
        event_type: str,
        evidence: dict[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, str]:
        if not agent_id:
            raise ValueError("agent_id is required to seal evidence in Gnomledger")
        event_id = self._gnomledger.record_execution_attestation(
            agent_id=agent_id,
            event_type=event_type,
            summary=f"CAPPO evidence seal for certificate {certificate_id}",
            details={"certificate_id": certificate_id, "evidence_seal": evidence},
        )
        if not event_id:
            raise RuntimeError("Gnomledger did not acknowledge the evidence event")
        return {"event_id": event_id}


def create_pgl_client(
    db: Any | None = None,
    settings: Settings | None = None,
    use_veklom: bool = True,
) -> PGLClient | VeklomPGLAdapter | GnomledgerPGLAdapter:
    """Factory to create appropriate PGL client.

    Precedence: gnomledger (canonical ledger) > veklom-byos-backend (legacy detour) > local DB.

    Args:
        db: Database session (for local fallback)
        settings: CAPPO settings
        use_veklom: If True, allow falling back to veklom when gnomledger is not set.

    Returns:
        PGL client (gnomledger adapter, veklom adapter, or local DB client)
    """
    settings = settings or get_settings()

    if settings.gnomledger_url:
        return GnomledgerPGLAdapter(db=db, settings=settings)

    if use_veklom and settings.veklom_byos_backend_url:
        logger.warning(
            "GNOMLEDGER_URL not set — falling back to veklom-byos-backend's "
            "local PGL implementation. Certificates minted this way are not "
            "in the canonical gnomledger ledger. Set GNOMLEDGER_URL to fix."
        )
        return VeklomPGLAdapter(db=db, settings=settings)

    return PGLClient(db=db, settings=settings)
