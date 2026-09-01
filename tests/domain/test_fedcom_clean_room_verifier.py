import pytest
from datetime import datetime, timezone, timedelta
from cappo_backend.security.fedcom_verifier import (
    FedcomCleanRoomVerifier, 
    SignatureVerificationError, 
    EnvelopeExpiredError
)
from tests.fixtures.fedcom_fixtures import generate_valid_envelope, TEST_SECRET_KEY

def test_clean_room_verifier_success():
    """Verify that a valid envelope passes the clean-room verification."""
    verifier = FedcomCleanRoomVerifier(secret_key=TEST_SECRET_KEY)
    envelope = generate_valid_envelope()
    
    # Should not raise an exception
    assert verifier.verify_envelope(envelope) is True

def test_clean_room_verifier_signature_failure():
    """Verify that a tampered envelope raises a SignatureVerificationError."""
    verifier = FedcomCleanRoomVerifier(secret_key=TEST_SECRET_KEY)
    envelope = generate_valid_envelope()
    
    # Tamper with the workload hash
    envelope.workload_hash = "tampered_hash_123"
    
    with pytest.raises(SignatureVerificationError):
        verifier.verify_envelope(envelope)

def test_clean_room_verifier_expiration_failure():
    """Verify that an expired envelope raises an EnvelopeExpiredError."""
    verifier = FedcomCleanRoomVerifier(secret_key=TEST_SECRET_KEY)
    envelope = generate_valid_envelope()
    
    # We must mathematically re-sign it so that the signature is valid, 
    # but the expiration time is in the past. This isolates the expiration check.
    past_time = datetime.now(timezone.utc) - timedelta(hours=1)
    envelope.expires_at = past_time
    
    # Re-sign the envelope mathematically via the fixture logic 
    from tests.fixtures.fedcom_fixtures import sign_envelope
    envelope.signature = sign_envelope(envelope)
    
    with pytest.raises(EnvelopeExpiredError):
        verifier.verify_envelope(envelope)

