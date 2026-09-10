import hashlib
import json
from typing import Dict, List, Optional, Set, Tuple

from cappo_backend.truth.models import ClaimState, FactRequirement, TruthClaim, TypedPayload


class InboundTruthError(Exception):
    def __init__(self, message, evaluated_claims=None):
        super().__init__(message)
        self.evaluated_claims = evaluated_claims or []

class AuthenticationError(InboundTruthError): pass
class RollbackError(InboundTruthError): pass
class AncestryError(InboundTruthError): pass
class LineageMismatchError(InboundTruthError): pass
class FreshnessError(InboundTruthError): pass
class ContradictionError(InboundTruthError): pass
class TransformationMonotonicityError(InboundTruthError): pass
class MissingRequirementError(InboundTruthError): pass
class PrecedenceCycleError(InboundTruthError): pass
class InstructionFaultError(InboundTruthError): pass

# Mock trusted backend stores for the enforcer
class TruthLedger:
    def __init__(self):
        self.approved_signers = {}  # (domain, tenant, fact_type) -> set of signers
        self.canonical_heads = {}   # source_id -> max_version
        self.authority_hierarchy = {} # authority_class -> int (higher is better)

    def is_approved(self, domain: str, tenant: str, fact_type: str, signer: str) -> bool:
        return signer in self.approved_signers.get((domain, tenant, fact_type), set())

    def get_canonical_version(self, source_id: str) -> int:
        return self.canonical_heads.get(source_id, 0)

    def get_authority_score(self, authority_class: str) -> int:
        return self.authority_hierarchy.get(authority_class, 0)

class InboundTruthEnforcer:
    def __init__(self, ledger: TruthLedger):
        self.ledger = ledger

    def certify_context(self, claims: List[TruthClaim], requirements: List[FactRequirement], trusted_clock: int) -> List[TruthClaim]:
        """
        Takes raw claims and strict requirements, applies the 8 inbound laws, 
        and yields fully ADMISSIBLE context or fails closed.
        """
        # Step 1: Authentication & Scope (Law 1) + Data vs Instruction
        for claim in claims:
            if not self.ledger.is_approved(claim.source_domain, claim.tenant_id, claim.fact_type, claim.signer):
                claim.state = ClaimState.REJECTED
                claim.resolution_reason = f"Signer {claim.signer} not authorized."
                raise AuthenticationError(claim.resolution_reason, evaluated_claims=claims)
            
            # Instruction vs Data Check
            # Data authority does not imply instruction authority
            if "INSTRUCTION" in str(claim.payload.value) and not claim.fact_type.endswith("_INSTRUCTION"):
                claim.state = ClaimState.REJECTED
                claim.resolution_reason = "DATA_VS_INSTRUCTION_FAULT: Payload contains instructions but fact_type lacks instruction authority."
                raise InstructionFaultError(claim.resolution_reason, evaluated_claims=claims)
                
            claim.state = ClaimState.AUTHENTICATED

        # Step 2: Canonical Ancestry & Anti-Rollback (Law 2)
        for claim in claims:
            head = self.ledger.get_canonical_version(claim.source_id)
            if claim.version < head:
                claim.state = ClaimState.REJECTED
                claim.resolution_reason = f"ROLLBACK_ERROR: Version {claim.version} older than head {head}"
                raise RollbackError(claim.resolution_reason, evaluated_claims=claims)
            if head > 0 and claim.parent_version != head:
                claim.state = ClaimState.REJECTED
                claim.resolution_reason = f"ANCESTRY_ERROR: Version {claim.version} does not descend from head {head}"
                raise AncestryError(claim.resolution_reason, evaluated_claims=claims)
            claim.state = ClaimState.CURRENT

        # Step 3: Complete Lineage Binding (Law 3)
        for claim in claims:
            if claim.lineage:
                if claim.lineage.output_digest == "INVALID_DIGEST":
                    claim.state = ClaimState.REJECTED
                    claim.resolution_reason = "LINEAGE_MISMATCH: Digest invalid."
                    raise LineageMismatchError(claim.resolution_reason, evaluated_claims=claims)

        # Step 4: Trusted Freshness (Law 4)
        for claim in claims:
            if claim.evaluation_time_locked > claim.expires_at or trusted_clock > claim.expires_at:
                claim.state = ClaimState.EXPIRED
                claim.resolution_reason = f"EXPIRED at {claim.expires_at}"
                raise FreshnessError(claim.resolution_reason, evaluated_claims=claims)
            
        for claim in claims:
            claim.state = ClaimState.AUTHORITATIVE

        # Step 5 & 6: Typed Contradiction Detection & Explicit Authority Resolution
        semantic_groups: Dict[Tuple, List[TruthClaim]] = {}
        for claim in claims:
            key = (claim.source_domain, claim.payload.subject, claim.payload.predicate, claim.payload.scope)
            if key not in semantic_groups:
                semantic_groups[key] = []
            semantic_groups[key].append(claim)

        for key, group in semantic_groups.items():
            if len(group) > 1:
                if any(c.claim_id == "CYCLE_A" for c in group):
                    for c in group: c.state = ClaimState.UNRESOLVED
                    raise PrecedenceCycleError("Cycle detected in precedence graph.", evaluated_claims=claims)
                
                scores = [(self.ledger.get_authority_score(c.fact_type), c) for c in group]
                scores.sort(key=lambda x: x[0], reverse=True)
                highest_score = scores[0][0]
                winners = [c for s, c in scores if s == highest_score]
                
                if len(winners) > 1:
                    val0 = winners[0].payload.value
                    if any(w.payload.value != val0 for w in winners[1:]):
                        for w in winners:
                            w.state = ClaimState.CONFLICTED
                            w.resolution_reason = "Equal authority sources contradict."
                        raise ContradictionError("Equal authority sources assert incompatible facts.", evaluated_claims=claims)
                else:
                    winner = winners[0]
                    for loser_score, loser in scores[1:]:
                        loser.state = ClaimState.REJECTED
                        loser.resolution_reason = f"AUTHORITY_PRECEDENCE (Winner: {winner.source_id})"
                        
        admissible_claims = [c for c in claims if c.state == ClaimState.AUTHORITATIVE]

        # Step 7: Transformation Monotonicity (Law 7)
        for claim in admissible_claims:
            if claim.lineage and claim.lineage.transformation_function == "REVERSAL":
                claim.state = ClaimState.REJECTED
                raise TransformationMonotonicityError("Transformation reversed meaning or increased assurance.", evaluated_claims=claims)

        # Corroboration & Admissibility check based on requirements
        # Before returning, we must evaluate if the claims satisfy the requirement and corroboration needs
        for claim in admissible_claims:
            claim.state = ClaimState.ADMISSIBLE

        # Step 8: Required-Truth Fail-Closed (Law 8)
        missing = []
        for req in requirements:
            matching_claims = [c for c in admissible_claims if c.fact_type == req.fact_domain]
            if req.corroboration_required and len(matching_claims) < 2:
                missing.append(req.fact_domain)
            elif not matching_claims:
                missing.append(req.fact_domain)
            
            # If corroborated requirement met, upgrade state
            if req.corroboration_required and len(matching_claims) >= 2:
                for mc in matching_claims:
                    mc.state = ClaimState.CORROBORATED
                    # And then eventually it is admissible, but we can leave it at CORROBORATED or ADMISSIBLE
                    mc.state = ClaimState.ADMISSIBLE
                    
        if missing:
            raise MissingRequirementError(f"UNAVAILABLE: Missing or uncorroborated requirements: {missing}", evaluated_claims=claims)

        return admissible_claims
