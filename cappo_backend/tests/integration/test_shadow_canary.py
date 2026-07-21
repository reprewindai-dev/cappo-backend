from datetime import datetime, timezone

from cappo_backend.services.governance import (
    Policy,
    PolicyCompositionEngine,
    PolicyRule,
    effective_permissions,
)
from cappo_backend.services.promotion_compiler import (
    EvidenceEnvelope,
    MaturityState,
    PromotionCompiler,
)


def test_shadow_canary_halt_on_insufficient_evidence():
    """
    Simulate a shadow canary workflow where an agent attempts a high-risk capability.
    The Promotion Compiler evaluates evidence, but since PGL anchoring failed,
    the maturity state remains below AUTHORIZED_FOR_PRODUCTION.
    The Governance gate must deterministically block execution.
    """
    compiler = PromotionCompiler()
    
    # 1. Agent submits a shadow-mode execution with incomplete evidence (missing PGL certificate).
    evidence_chain = [
        EvidenceEnvelope(
            evidence_id="ev-1",
            evidence_type="commit_hash",
            timestamp=datetime.now(timezone.utc),
            issuer="system",
            payload_hash="abcd",
        ),
        EvidenceEnvelope(
            evidence_id="ev-2",
            evidence_type="shadow_success_log",
            timestamp=datetime.now(timezone.utc),
            issuer="shadow_worker",
            payload_hash="efgh",
        ),
        # MISSING: "pgl_certificate"
    ]
    
    promotion_state = compiler.evaluate("cap-critical-action", evidence_chain)
    
    # Without PGL Certificate, it should not reach PRODUCTION_CANDIDATE
    assert promotion_state.current_state.value < MaturityState.PRODUCTION_CANDIDATE.value

    # 2. CAPPO Governance evaluates the execution graph
    engine = PolicyCompositionEngine()
    
    system_policy = Policy(
        policy_id="sys-global",
        rules=[PolicyRule(effect="allow", description="Global Allow")],
        maturity_required=MaturityState.AUTHORIZED_FOR_PRODUCTION,
    )
    
    composition = engine.compose(
        agent_id="agent-007",
        capability_id="cap-critical-action",
        system_policy=system_policy,
    )
    
    # 3. Final effective permissions check
    perms = effective_permissions(
        composition,
        trust_current=100.0,
        maturity_current=promotion_state.current_state,
    )
    
    # Ensure the execution is safely blocked at the gate
    assert perms.can_execute is False
    assert perms.maturity_current == promotion_state.current_state
    assert perms.maturity_required == MaturityState.AUTHORIZED_FOR_PRODUCTION


def test_shadow_canary_success_on_complete_evidence():
    """
    Simulate a shadow canary workflow where the agent has collected all required
    cryptographic evidence, including PGL certificates and human approval.
    """
    compiler = PromotionCompiler()
    
    evidence_chain = [
        EvidenceEnvelope(
            evidence_id="ev-1", evidence_type="commit_hash", timestamp=datetime.now(timezone.utc),
            issuer="system", payload_hash="hash1",
        ),
        EvidenceEnvelope(
            evidence_id="ev-2", evidence_type="local_test_pass", timestamp=datetime.now(timezone.utc),
            issuer="ci", payload_hash="hash2",
        ),
        EvidenceEnvelope(
            evidence_id="ev-3", evidence_type="integration_test_pass", timestamp=datetime.now(timezone.utc),
            issuer="ci", payload_hash="hash3",
        ),
        EvidenceEnvelope(
            evidence_id="ev-4", evidence_type="shadow_success_log", timestamp=datetime.now(timezone.utc),
            issuer="shadow", payload_hash="hash4",
        ),
        EvidenceEnvelope(
            evidence_id="ev-5", evidence_type="pgl_certificate", timestamp=datetime.now(timezone.utc),
            issuer="pgl_anchor", payload_hash="hash5",
        ),
        EvidenceEnvelope(
            evidence_id="ev-6", evidence_type="human_promotion_signature", timestamp=datetime.now(timezone.utc),
            issuer="admin", payload_hash="hash6",
        ),
    ]
    
    promotion_state = compiler.evaluate("cap-critical-action", evidence_chain)
    
    # With all evidence, it should reach AUTHORIZED_FOR_PRODUCTION
    assert promotion_state.current_state == MaturityState.AUTHORIZED_FOR_PRODUCTION

    engine = PolicyCompositionEngine()
    
    system_policy = Policy(
        policy_id="sys-global",
        rules=[PolicyRule(effect="allow", description="Global Allow")],
        maturity_required=MaturityState.AUTHORIZED_FOR_PRODUCTION,
        trust_required=90.0,
    )
    
    composition = engine.compose(
        agent_id="agent-007",
        capability_id="cap-critical-action",
        system_policy=system_policy,
    )
    
    perms = effective_permissions(
        composition,
        trust_current=100.0,
        maturity_current=promotion_state.current_state,
    )
    
    # Ensure the execution is allowed
    assert perms.can_execute is True
