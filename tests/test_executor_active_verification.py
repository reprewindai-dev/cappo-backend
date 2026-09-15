import pytest
from cappo_backend.services.executor import ResilientExecutor, Provider, EchoExecutor, ExecutorUnavailableError
from cappo_backend.services.active_verification import registry, VerifierModule, VerifiedRuntimeContext, _trusted_connection_info
import time

class MockHyperVVerifier(VerifierModule):
    is_mock = True
    
    def discover(self, connection_info: dict) -> bool:
        return connection_info.get("substrate_hint") == "hyper-v"
        
    def challenge(self, connection_info: dict) -> str:
        return "mock_nonce"
        
    def measure(self, connection_info: dict, nonce: str) -> dict:
        return {"hyperv_vm_id": connection_info.get("instance_hint", "VM-12345"), "epoch": 1, "nonce": nonce}
        
    def verify(self, measurement: dict, nonce: str) -> bool:
        return bool(measurement.get("hyperv_vm_id")) and measurement.get("nonce") == nonce
        
    def attest(self, measurement: dict) -> VerifiedRuntimeContext:
        return VerifiedRuntimeContext(
            substrate_kind="hyper-v",
            substrate_instance_id=measurement["hyperv_vm_id"],
            boot_instance_id="boot_hash_abc123",
            runtime_identity="hyperv-guest-identity",
            verifier_method="HOST_HYPERVISOR_API",
            verifier_identity="cappo-hyperv-host",
            authority_epoch=measurement["epoch"],
            package_digest="pkg-hash",
            state_root="state-root",
            challenge_nonce=measurement["nonce"],
            observed_at=str(int(time.time())),
            expires_at=str(int(time.time()) + 3600),
            measurement_digest="measure-hash",
            evidence_level="HIGH_ASSURANCE"
        )

# Register a mock verifier specifically for testing
try:
    registry.register("hyper-v-test-verifier", MockHyperVVerifier())
    # We do NOT freeze the registry here so other tests can still register mocks,
    # or we freeze it if this is an isolated test environment.
except RuntimeError:
    pass

def test_executor_denies_stale_caller_at_consequence_boundary():
    from cappo_backend.services.circuit_breaker import CircuitBreaker
    
    # Live mode is used to prove fail-closed and strict binding verification
    # Note: we temporarily un-mock the HyperVVerifier for the live test
    original_is_mock = MockHyperVVerifier.is_mock
    MockHyperVVerifier.is_mock = False
    import sys
    # Disable universal mock if it exists
    from cappo_backend.services.active_verification import registry
    if 'universal_mock' in registry._modules:
        registry._modules['universal_mock'].is_mock = False

    
    try:
        executor = ResilientExecutor([Provider("echo:test", EchoExecutor(), CircuitBreaker())], execution_mode="live")
        
        # Simulate minting (this would usually be done by the orchestrator)
        mint_connection_info = {"substrate_hint": "hyper-v", "instance_hint": "VM-12345"}
        mint_context = registry.execute_active_verification(mint_connection_info, execution_mode="test")
        
        authority_envelope = {
            "execution_id": "test-execution",
            "allowed_provider_set": ["echo:test"],
            "runtime_incarnation_binding_hash": mint_context.get_runtime_incarnation_binding_hash()
        }
        
        good_request = {
            "prompt": "hello",
            "authority_envelope": authority_envelope
        }
        
        # 1. Good Execution (Sink observes physical caller perfectly matches EI binding)
        _trusted_connection_info.set({"substrate_hint": "hyper-v", "instance_hint": "VM-12345"})
        res = executor.execute(good_request)
        assert res["response"] == "echo: hello"
        
        # 2. Hostile Sink Replay (Physical Connection Info changed!)
        _trusted_connection_info.set({"substrate_hint": "hyper-v", "instance_hint": "VM-HOSTILE"})
        with pytest.raises(ExecutorUnavailableError, match="DENY BEFORE EFFECT: STALE AUTHORITY / HOSTILE IDENTITY TRANSITION"):
            executor.execute(good_request)
            
        # 3. Missing Binding Fail-Closed Proof (Live Mode)
        bad_request = {
            "prompt": "hello",
            "authority_envelope": {
                "execution_id": "test-execution",
                "allowed_provider_set": ["echo:test"]
                # Missing runtime_incarnation_binding_hash
            }
        }
        with pytest.raises(ExecutorUnavailableError, match="DENY BEFORE EFFECT: Missing runtime_incarnation_binding_hash"):
            executor.execute(bad_request)
            
        # 4. Missing Envelope Fail-Closed Proof (Live Mode)
        empty_request = {
            "prompt": "hello"
        }
        with pytest.raises(ExecutorUnavailableError, match="DENY BEFORE EFFECT: Missing authority envelope"):
            executor.execute(empty_request)
            
    finally:
        # Restore mock state
        MockHyperVVerifier.is_mock = original_is_mock
