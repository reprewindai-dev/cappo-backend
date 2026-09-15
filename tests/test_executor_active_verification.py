import pytest
from cappo_backend.services.executor import ResilientExecutor, Provider, EchoExecutor, ExecutorUnavailableError
from cappo_backend.services.active_verification import registry, HyperVVerifier

# Ensure mock verifier is registered
try:
    registry.register("hyper-v-mock", HyperVVerifier())
except RuntimeError:
    pass

def test_executor_denies_stale_caller_at_consequence_boundary():
    # Setup executor
    from cappo_backend.services.circuit_breaker import CircuitBreaker
    executor = ResilientExecutor([Provider("echo:test", EchoExecutor(), CircuitBreaker())])
    
    # Simulate a minted authority envelope
    # The EI was minted for VM-12345
    mint_connection_info = {"substrate_hint": "hyper-v", "instance_hint": "VM-12345"}
    mint_context = registry.execute_active_verification(mint_connection_info, execution_mode="test")
    
    authority_envelope = {
        "execution_id": "test-execution",
        "allowed_provider_set": ["echo:test"],
        "runtime_incarnation_binding_hash": mint_context.get_runtime_incarnation_binding_hash()
    }
    
    # 1. Good Execution (Sink verifies physical caller perfectly matches EI binding)
    good_request = {
        "prompt": "hello",
        "authority_envelope": authority_envelope,
        "_physical_connection_info": {"substrate_hint": "hyper-v", "instance_hint": "VM-12345"}
    }
    
    res = executor.execute(good_request)
    assert res["response"] == "echo: hello"
    
    # 2. Hostile Sink Replay (Physical Connection Info changed!)
    # We simulate a stale caller reaching the Sink by intercepting the physical_connection_info
    hostile_request = {
        "prompt": "hello",
        "authority_envelope": authority_envelope,
        "_physical_connection_info": {"substrate_hint": "hyper-v", "instance_hint": "VM-HOSTILE"}
    }
    
    with pytest.raises(ExecutorUnavailableError, match="DENY BEFORE EFFECT: STALE AUTHORITY / HOSTILE IDENTITY TRANSITION"):
        executor.execute(hostile_request)
