import pytest
import time
import uuid
from cappo_backend.services.active_verification import registry, VerifiedRuntimeContext
from cappo_backend.services.hyperv_verifier import HyperVVerifier
from cappo_backend.services.orchestrator import RunOrchestrator, RuntimeOwnershipError
from unittest.mock import MagicMock, patch

@pytest.fixture(autouse=True)
def setup_teardown_verifier():
    # Make sure HyperVVerifier is registered
    if "hyperv" not in registry._modules:
        registry._frozen = False
        registry.register("hyperv", HyperVVerifier())
    yield
    registry._frozen = False

def test_physical_assault_stale_a_deny(db_session, pgl_client, audit_service):
    # This test simulates a physical assault where a suspended VM (A)
    # wakes up after the path has been reassigned to VM (B).
    
    # 1. Setup orchestrator
    executor = MagicMock()
    executor.execute.return_value = {"status": "success"}
    builder = MagicMock()
    builder.build.return_value = {
        "execution_id": "test-run-1",
        "pgl_certificate_id": "cert-1",
        "subject": "{}",
        "capabilities": "{}",
        "delegation": "{}",
        "budget": "{}",
        "authority_bundle_hash": "hash1",
        "policy_hash": "hash2",
        "expires_at": "2030-01-01T00:00:00Z",
        "signature": "sig",
        "ei_id": "ei-1"
    }

    orchestrator = RunOrchestrator(
        db=db_session,
        pgl=pgl_client,
        builder=builder,
        executor=executor,
        audit=audit_service,
        execution_mode="live"
    )

    # VM A properties
    vm_a_system_id = "vm-a-1234"
    vm_a_boot_id = str(uuid.uuid4())
    
    # Run orchestrator mint phase (Claim ownership) for VM A
    run = orchestrator.create_run({
        "directive": "ALLOW",
        "budget_approved_cents": 100
    })
    orchestrator.compile_run(run)
    orchestrator.contextualize_run(run)
    orchestrator.govern_run(run)
    orchestrator.commit_run(run)

    # Patch connection info to simulate VM A for Mint
    with patch("cappo_backend.services.orchestrator.registry.execute_active_verification") as mock_exec:
        # Mock what HyperVVerifier would return for VM A
        mock_exec.return_value = VerifiedRuntimeContext(
            substrate_kind="hyper-v",
            substrate_instance_id=vm_a_system_id,
            boot_instance_id=vm_a_boot_id,
            runtime_identity="hyperv-runtime",
            verifier_method="hyperv-hcs-attestation",
            verifier_identity="hyperv-host",
            authority_epoch=1,
            package_digest="pkg1",
            state_root="root1",
            challenge_nonce="nonce1",
            observed_at=str(time.time()),
            expires_at=str(time.time() + 300),
            measurement_digest="meas-a",
            evidence_level="hcs-enclave"
        )
        orchestrator.mint_execution_identity(run)

    # Now VM A executes successfully
    with patch("cappo_backend.services.orchestrator.registry.execute_active_verification") as mock_exec2:
        mock_exec2.return_value = VerifiedRuntimeContext(
            substrate_kind="hyper-v",
            substrate_instance_id=vm_a_system_id,
            boot_instance_id=vm_a_boot_id,
            runtime_identity="hyperv-runtime",
            verifier_method="hyperv-hcs-attestation",
            verifier_identity="hyperv-host",
            authority_epoch=1,
            package_digest="pkg1",
            state_root="root1",
            challenge_nonce="nonce2", # fresh nonce
            observed_at=str(time.time()),
            expires_at=str(time.time() + 300),
            measurement_digest="meas-a",
            evidence_level="hcs-enclave"
        )
        orchestrator.route_run(run)
        orchestrator.validate_with_capi(run)
        orchestrator.execute_run(run)
        orchestrator.attest_run(run)
        
    assert executor.execute.call_count == 1

    # 2. Transition A -> B
    # A new run is created and assigned to VM B (epoch 2)
    vm_b_system_id = "vm-b-5678"
    vm_b_boot_id = str(uuid.uuid4())
    
    run_b = orchestrator.create_run({
        "directive": "ALLOW",
        "budget_approved_cents": 100
    })
    orchestrator.compile_run(run_b)
    orchestrator.contextualize_run(run_b)
    orchestrator.govern_run(run_b)
    orchestrator.commit_run(run_b)

    with patch("cappo_backend.services.orchestrator.registry.execute_active_verification") as mock_exec_b:
        mock_exec_b.return_value = VerifiedRuntimeContext(
            substrate_kind="hyper-v",
            substrate_instance_id=vm_b_system_id,
            boot_instance_id=vm_b_boot_id,
            runtime_identity="hyperv-runtime",
            verifier_method="hyperv-hcs-attestation",
            verifier_identity="hyperv-host",
            authority_epoch=2,
            package_digest="pkg1",
            state_root="root1",
            challenge_nonce="nonce3",
            observed_at=str(time.time()),
            expires_at=str(time.time() + 300),
            measurement_digest="meas-b",
            evidence_level="hcs-enclave"
        )
        orchestrator.mint_execution_identity(run_b)

    # 3. A stale resumes and tries to execute with B's path ID (or old path ID)
    # Let's say VM A replays its execution identity but host observes A instead of B.
    # The EI ownership bound to run_b expects epoch 2 and VM B.
    
    # We patch active verification to observe VM A's stale state
    with patch("cappo_backend.services.orchestrator.registry.execute_active_verification") as mock_exec_stale_a:
        mock_exec_stale_a.return_value = VerifiedRuntimeContext(
            substrate_kind="hyper-v",
            substrate_instance_id=vm_a_system_id,
            boot_instance_id=vm_a_boot_id,
            runtime_identity="hyperv-runtime",
            verifier_method="hyperv-hcs-attestation",
            verifier_identity="hyperv-host",
            authority_epoch=1,  # Stale epoch
            package_digest="pkg1",
            state_root="root1",
            challenge_nonce="nonce4",
            observed_at=str(time.time()),
            expires_at=str(time.time() + 300),
            measurement_digest="meas-a",
            evidence_level="hcs-enclave"
        )
        
        # A reaches real Sink (execute_run)
        with pytest.raises(RuntimeOwnershipError, match="STALE AUTHORITY / HOSTILE IDENTITY TRANSITION"):
            orchestrator.route_run(run_b)
            orchestrator.execute_run(run_b)
            
    # Provider call should not increment
    assert executor.execute.call_count == 1
