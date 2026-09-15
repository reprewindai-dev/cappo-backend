import pytest
import time
from cappo_backend.services.active_verification import (
    VerifiedRuntimeContext,
    VerifierModule,
    ActiveVerifierRegistry,
    registry
)

def test_registry_substitution_deny():
    test_reg = ActiveVerifierRegistry()
    class Dummy(VerifierModule):
        pass
    test_reg.register("dummy", Dummy())
    with pytest.raises(RuntimeError, match="Duplicate verifier name"):
        test_reg.register("dummy", Dummy())

def test_missing_verifier_deny():
    test_reg = ActiveVerifierRegistry()
    with pytest.raises(RuntimeError, match="No registered VerifierModule could establish"):
        test_reg.execute_active_verification({"substrate_hint": "unknown"})

def test_mock_verifier_forbidden_in_live():
    test_reg = ActiveVerifierRegistry()
    class MockV(VerifierModule):
        is_mock = True
        def discover(self, c): return True
    test_reg.register("mock", MockV())
    with pytest.raises(RuntimeError, match="FATAL/DENY: Mock verifier forbidden"):
        test_reg.execute_active_verification({}, execution_mode="live")
        
def test_verifier_exception_deny():
    test_reg = ActiveVerifierRegistry()
    class BadV(VerifierModule):
        def discover(self, c): return True
        def challenge(self, c): raise ValueError("Socket dead")
    test_reg.register("bad", BadV())
    with pytest.raises(ValueError, match="Socket dead"):
        test_reg.execute_active_verification({})

def test_expired_context_deny():
    test_reg = ActiveVerifierRegistry()
    class ExpiredV(VerifierModule):
        def discover(self, c): return True
        def challenge(self, c): pass
        def measure(self, c): return {}
        def verify(self, m): return True
        def attest(self, m):
            return VerifiedRuntimeContext(
                substrate_kind="test", substrate_instance_id="1", boot_instance_id="boot",
                runtime_identity="id", verifier_method="meth", verifier_identity="v_id",
                authority_epoch=1, package_digest="d", state_root="s", challenge_nonce="c",
                observed_at="100", expires_at="100", measurement_digest="m", evidence_level="E"
            ) # expired! time.time() > 100
    test_reg.register("exp", ExpiredV())
    with pytest.raises(ValueError, match="Context expired"):
        test_reg.execute_active_verification({})

def test_context_hash_immutability():
    ctx1 = VerifiedRuntimeContext(
        substrate_kind="test", substrate_instance_id="1", boot_instance_id="boot",
        runtime_identity="id", verifier_method="meth", verifier_identity="v_id",
        authority_epoch=1, package_digest="d", state_root="s", challenge_nonce="c_old",
        observed_at="100", expires_at="9999999999", measurement_digest="m", evidence_level="E"
    )
    
    # Simulating a fresh verification at execution time (same boot, but fresh time/nonce)
    ctx2 = VerifiedRuntimeContext(
        substrate_kind="test", substrate_instance_id="1", boot_instance_id="boot",
        runtime_identity="id", verifier_method="meth", verifier_identity="v_id",
        authority_epoch=1, package_digest="d", state_root="s", challenge_nonce="c_new",
        observed_at="200", expires_at="9999999999", measurement_digest="m", evidence_level="E"
    )
    
    # Full evidence hash MUST differ (TOCTOU protection against stale observation replay)
    assert ctx1.get_full_evidence_hash() != ctx2.get_full_evidence_hash()
    
    # Stable execution binding MUST match (Valid execution)
    assert ctx1.get_runtime_incarnation_binding_hash() == ctx2.get_runtime_incarnation_binding_hash()
    
    # Now simulate hijacked boot (PID recycled)
    ctx3 = VerifiedRuntimeContext(
        substrate_kind="test", substrate_instance_id="1", boot_instance_id="hijacked_boot",
        runtime_identity="id", verifier_method="meth", verifier_identity="v_id",
        authority_epoch=1, package_digest="d", state_root="s", challenge_nonce="c_new2",
        observed_at="300", expires_at="9999999999", measurement_digest="m", evidence_level="E"
    )
    
    # Stable execution binding MUST differ (DENY BEFORE EFFECT)
    assert ctx1.get_runtime_incarnation_binding_hash() != ctx3.get_runtime_incarnation_binding_hash()
