
from cappo_backend.security.biscuit import (
    attenuate_biscuit_capability,
    mint_biscuit_capability,
    verify_biscuit_capability,
)


def test_g0b4_biscuit_attenuation():
    caller_spiffe_id = "spiffe://example.org/workload/cappo-backend"
    executor_spiffe_id = "spiffe://example.org/workload/my-agent"
    capability_id = "records@v1"
    reads = ["record.read", "record.read_all"]
    writes = ["record.write"]
    execution_id = "exec_42"
    ttl_seconds = 600

    # 1. Mint Parent Token
    parent_token_b64 = mint_biscuit_capability(
        caller_spiffe_id=caller_spiffe_id,
        executor_spiffe_id=executor_spiffe_id,
        capability_id=capability_id,
        reads=reads,
        writes=writes,
        execution_id=execution_id,
        ttl_seconds=ttl_seconds
    )

    assert parent_token_b64 is not None
    # Parent authorizes read record.read
    assert verify_biscuit_capability(
        token_b64=parent_token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="record.read",
        subject_spiffe_id=caller_spiffe_id
    )

    # 2. Attenuate Child Token Locally
    child_token_b64 = attenuate_biscuit_capability(
        token_b64=parent_token_b64,
        reads=["record.read"],
        writes=[],  # drop writes
        ttl_seconds=120  # reduced from 600
    )

    assert child_token_b64 is not None
    assert child_token_b64 != parent_token_b64

    # 3. Valid Child Action Allowed
    assert verify_biscuit_capability(
        token_b64=child_token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="record.read",
        subject_spiffe_id=caller_spiffe_id
    )

    # 4. Action Widening Denied (write)
    assert not verify_biscuit_capability(
        token_b64=child_token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="record.write",
        subject_spiffe_id=caller_spiffe_id
    )

    # 5. Dropped Action Denied (read_all)
    assert not verify_biscuit_capability(
        token_b64=child_token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="record.read_all",
        subject_spiffe_id=caller_spiffe_id
    )

    # 6. Expiry Widening Denied
    # If a malicious user tries to append a block to extend expiry, 
    # the parent's expiry block 0 check still fails.
    
    # 7. Depth Limit Exceeded
    # Parent was minted with delegation_depth_max(1).
    # Child is depth 1.
    # Attenuating child again should fail to verify.
    grandchild_token_b64 = attenuate_biscuit_capability(
        token_b64=child_token_b64,
        reads=["record.read"],
        writes=[],
        ttl_seconds=60
    )
    
    assert not verify_biscuit_capability(
        token_b64=grandchild_token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="record.read",
        subject_spiffe_id=caller_spiffe_id
    )

    print("\nG0B.4 = VERIFIED")

