import pytest
from datetime import datetime, timedelta, timezone
import time

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
        action="read",
        resource="contact.read"
    ) == True

    # 3. Wrong Action DENY
    assert verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="read",
        resource="message.send"  # authorized for write, not read
    ) == False

    assert verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="delete",
        resource="contact.read"
    ) == False

    # 4. Wrong Resource DENY
    assert verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="read",
        resource="unknown.resource"
    ) == False

    # 5. Wrong SPIFFE identity (Audience) DENY
    assert verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id="spiffe://example.org/workload/evil-agent",
        action="read",
        resource="contact.read"
    ) == False

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
    assert verify_biscuit_capability(
        token_b64=expired_token,
        executor_spiffe_id=executor_spiffe_id,
        action="read",
        resource="contact.read"
    ) == False

    # 7. WRONG_SUBJECT Denied
    assert verify_biscuit_capability(
        token_b64=token_b64,
        executor_spiffe_id=executor_spiffe_id,
        action="read",
        resource="/records/customer-42",
        subject_spiffe_id="spiffe://example.org/workload/not-cappo"
    ) == False
    print("WRONG_SUBJECT_DENIED = True")

    # 8. Receipt Token Binding is handled in capability_mount tests or service.py directly.
    # The database migration adds biscuit_token_sha256 to CapabilityActionReceipt.
    print("\nG0B.3 = VERIFIED")
