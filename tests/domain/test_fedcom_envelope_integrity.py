import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from tests.fixtures.fedcom_fixtures import generate_valid_envelope, sign_envelope, verify_envelope


def test_fedcom_envelope_valid_signature():
    """Mathematically verify that a correctly signed envelope passes verification."""
    envelope = generate_valid_envelope()
    assert verify_envelope(envelope) is True

def test_fedcom_envelope_tamper_workload():
    """Mathematically verify that tampering with the workload invalidates the envelope."""
    envelope = generate_valid_envelope()
    
    # Simulate a TLS-terminating proxy swapping the requested workload
    malicious_workload = b"prompt: malicious action"
    envelope.workload_hash = hashlib.sha256(malicious_workload).hexdigest()
    
    # The signature must fail
    assert verify_envelope(envelope) is False

def test_fedcom_envelope_tamper_execution_identity():
    """Mathematically verify that swapping the capability lease (execution_id) invalidates the envelope."""
    envelope = generate_valid_envelope()
    
    # Simulate an attacker trying to use someone else's execution_id
    envelope.execution_identity = "exec_stolen_xyz987"
    
    assert verify_envelope(envelope) is False

def test_fedcom_envelope_tamper_audience():
    """Mathematically verify that re-routing the envelope to a different node invalidates it."""
    envelope = generate_valid_envelope()
    
    # Simulate re-routing
    envelope.audience = "did:node:malicious"
    
    assert verify_envelope(envelope) is False

def test_fedcom_envelope_resigning_fails_without_key():
    """Mathematically verify that an attacker cannot resign the envelope without the private key."""
    envelope = generate_valid_envelope()
    
    envelope.workload_hash = hashlib.sha256(b"prompt: malicious action").hexdigest()
    
    # The attacker attempts to forge the signature
    # (Since they don't have TEST_SECRET_KEY, they might try to use a blank or random key)
    import hmac
    forged_sig = hmac.new(b"wrong_key", b"some data", hashlib.sha256).hexdigest()
    envelope.signature = forged_sig
    
    assert verify_envelope(envelope) is False

def test_fedcom_envelope_expiration_enforcement():
    """Verify that expired envelopes can be detected."""
    envelope = generate_valid_envelope()
    
    # Verify it is valid
    assert verify_envelope(envelope) is True
    
    # The application layer (not just the mathematical signature) must check expiration
    now = datetime.now(timezone.utc)
    assert envelope.expires_at > now
    
    # If we mathematically alter the expiration to bypass it, the signature fails
    envelope.expires_at = now + timedelta(days=365)
    assert verify_envelope(envelope) is False

