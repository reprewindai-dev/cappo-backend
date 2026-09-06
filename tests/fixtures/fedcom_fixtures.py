import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from cappo_backend.models.fedcom_v1 import (
    AdmissionDecision,
    VeklomExecutionEnvelope,
    VeklomTransitionReceipt,
)

# In a real environment, this would be an Ed25519 private key.
# For the prototype mathematical proof, we use HMAC-SHA256.
TEST_SECRET_KEY = b"fedcom-test-secret-key-1234567890"

def _canonicalize_envelope(envelope_data: Dict[str, Any]) -> bytes:
    """Canonicalize the envelope fields for stable hashing."""
    # We must exclude the signature itself!
    data_to_sign = {k: v for k, v in envelope_data.items() if k != "signature"}
    # Convert datetimes to isoformat strings for stability
    for k, v in data_to_sign.items():
        if isinstance(v, datetime):
            data_to_sign[k] = v.isoformat()
    return json.dumps(data_to_sign, sort_keys=True).encode("utf-8")

def sign_envelope(envelope: VeklomExecutionEnvelope) -> str:
    """Mathematically sign the envelope using HMAC-SHA256."""
    canonical_data = _canonicalize_envelope(envelope.model_dump())
    return hmac.new(TEST_SECRET_KEY, canonical_data, hashlib.sha256).hexdigest()

def verify_envelope(envelope: VeklomExecutionEnvelope) -> bool:
    """Verify that the envelope signature is mathematically valid."""
    expected_sig = sign_envelope(envelope)
    return hmac.compare_digest(expected_sig, envelope.signature)

def generate_valid_envelope() -> VeklomExecutionEnvelope:
    """Generate a valid, cryptographically signed Execution Envelope."""
    now = datetime.now(timezone.utc)
    env = VeklomExecutionEnvelope(
        envelope_id="env_123456",
        issuer="did:agent:test",
        audience="did:node:local",
        execution_identity="exec_test_abc123",
        workload_hash=hashlib.sha256(b"prompt: do something").hexdigest(),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        signature=""
    )
    env.signature = sign_envelope(env)
    return env

