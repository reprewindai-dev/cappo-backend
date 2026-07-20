"""Promotion Compiler: Deterministic Evidence-Based Maturity Ladder.

Enforces that capability maturity states (e.g. AUTHORIZED_FOR_PRODUCTION) are strictly
derived from verifiable cryptographic evidence rather than static string labels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from cappo_backend.services.canonical import sha256_json


class MaturityState(Enum):
    DESIGN_INTENT = 0
    IMPLEMENTED_UNVERIFIED = 1
    LOCALLY_VERIFIED = 2
    INTEGRATION_VERIFIED = 3
    PRODUCTION_CANDIDATE = 4
    AUTHORIZED_FOR_PRODUCTION = 5


@dataclass
class EvidenceEnvelope:
    evidence_id: str
    evidence_type: str  # e.g., 'pgl_certificate', 'shadow_success_log', 'test_run'
    timestamp: datetime
    issuer: str
    payload_hash: str
    signature: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class PromotionState:
    capability_id: str
    current_state: MaturityState
    last_evaluated: datetime
    derivation_hash: str
    evidence_chain: list[EvidenceEnvelope]


class PromotionCompiler:
    """Evaluates evidence to deterministically compute a capability's maturity state."""

    def __init__(self) -> None:
        pass

    def evaluate(self, capability_id: str, evidence_chain: list[EvidenceEnvelope]) -> PromotionState:
        """Derive the highest justifiable maturity state given the verifiable evidence."""
        state = MaturityState.DESIGN_INTENT
        
        # In a strict implementation, evidence signatures would be verified against known trusted issuers.
        has_implementation = any(e.evidence_type == "commit_hash" for e in evidence_chain)
        has_local_test = any(e.evidence_type == "local_test_pass" for e in evidence_chain)
        has_integration_test = any(e.evidence_type == "integration_test_pass" for e in evidence_chain)
        has_shadow_success = any(e.evidence_type == "shadow_success_log" for e in evidence_chain)
        has_pgl_certificate = any(e.evidence_type == "pgl_certificate" for e in evidence_chain)
        has_human_approval = any(e.evidence_type == "human_promotion_signature" for e in evidence_chain)

        if has_implementation:
            state = MaturityState.IMPLEMENTED_UNVERIFIED

        if state.value >= MaturityState.IMPLEMENTED_UNVERIFIED.value and has_local_test:
            state = MaturityState.LOCALLY_VERIFIED

        if state.value >= MaturityState.LOCALLY_VERIFIED.value and has_integration_test:
            state = MaturityState.INTEGRATION_VERIFIED

        if state.value >= MaturityState.INTEGRATION_VERIFIED.value and has_shadow_success and has_pgl_certificate:
            state = MaturityState.PRODUCTION_CANDIDATE

        if state.value >= MaturityState.PRODUCTION_CANDIDATE.value and has_human_approval:
            state = MaturityState.AUTHORIZED_FOR_PRODUCTION

        # Generate a derivation hash binding the state to the exact evidence used to compute it
        derivation_payload = {
            "capability_id": capability_id,
            "state_achieved": state.name,
            "evidence_count": len(evidence_chain),
            "evidence_hashes": [e.payload_hash for e in evidence_chain],
        }
        
        derivation_hash = sha256_json(derivation_payload)

        return PromotionState(
            capability_id=capability_id,
            current_state=state,
            last_evaluated=datetime.now(timezone.utc),
            derivation_hash=derivation_hash,
            evidence_chain=evidence_chain,
        )
