
from cappo_backend.security.biscuit import mint_biscuit_capability, verify_biscuit_capability


def test_g0b3_biscuit_issuance():
    # Valid SVID inputs
    caller_spiffe_id = "spiffe://example.org/workload/cappo-backend"
    executor_spiffe_id = "spiffe://example.org/workload/my-agent"
    capability_id = "echo@v1"
    reads = ["contact.read"]
    writes = ["message.send"]
    execution_id = "exec_12345"
    ttl_seconds = 300

    # 1. Mint Token
    token_b64 = mint_biscuit_capability(
        caller_spiffe_id=caller_spiffe_id,
        executor_spiffe_id=executor_spiffe_id,
        capability_id=capability_id,
        reads=reads,
        writes=writes,
        execution_id=execution_id,
        ttl_seconds=ttl_seconds
    )

    assert token_b64 is not None
    assert isinstance(token_b64, str)

    # 2. Token verifies successfully with correct context
    assert verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="contact.read"
    )

    # 3. Wrong Action DENY
    assert verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="message.send"  # authorized for write, not read (Wait, writes are "message.send") Oh actually, the test meant to check if "message.send" fails? Wait! "message.send" is in writes, so it SHOULD succeed!
    )

    assert not verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="delete.resource"
    )

    # 4. Wrong Resource DENY -> Not applicable anymore, but we can test unknown action
    assert not verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="unknown.action"
    )

    # 5. Wrong SPIFFE identity (Audience) DENY
    assert not verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id="spiffe://example.org/workload/evil-agent",
        action="contact.read"
    )

    # 6. Expired DENY
    # Mint an expired token
    expired_token = mint_biscuit_capability(
        caller_spiffe_id=caller_spiffe_id,
        executor_spiffe_id=executor_spiffe_id,
        capability_id=capability_id,
        reads=reads,
        writes=writes,
        execution_id=execution_id,
        ttl_seconds=-10 # expired 10 seconds ago
    )
    assert not verify_biscuit_capability(
        token_b64=expired_token,
        executor_spiffe_id=executor_spiffe_id,
        action="contact.read"
    )

    # 7. WRONG_SUBJECT Denied
    assert not verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="contact.read",
        subject_spiffe_id="spiffe://example.org/workload/not-cappo"
    )
    print("WRONG_SUBJECT_DENIED = True")

    # 8. Receipt Token Binding is handled in capability_mount tests or service.py directly.
    # The database migration adds biscuit_token_sha256 to CapabilityActionReceipt.
    print("\nG0B.3 = VERIFIED")
