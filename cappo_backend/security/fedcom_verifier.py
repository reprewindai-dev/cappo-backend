import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from cappo_backend.models.fedcom_v1 import VeklomExecutionEnvelope

class FedcomVerificationError(Exception):
    """Base exception for FEDCOM verification failures."""
    pass

class SignatureVerificationError(FedcomVerificationError):
    """Raised when the cryptographic signature does not match the payload."""
    pass

class EnvelopeExpiredError(FedcomVerificationError):
    """Raised when the envelope has passed its expiration time."""
    pass

class FedcomCleanRoomVerifier:
    """
    Standalone, decoupled verification layer for FEDCOM/0.1 Envelopes.
    Isolated from FastAPI endpoints to ensure zero-trust mathematically verifiable boundaries.
    """
    
    def __init__(self, secret_key: bytes):
        """
        Initialize the verifier with a secret key.
        In a full implementation, this would manage a key registry (SPIFFE/DID public keys).
        """
        self._secret_key = secret_key

    def _canonicalize(self, envelope_data: Dict[str, Any]) -> bytes:
        """Canonicalize the envelope fields for stable hashing."""
        data_to_sign = {k: v for k, v in envelope_data.items() if k != "signature"}
        for k, v in data_to_sign.items():
            if isinstance(v, datetime):
                data_to_sign[k] = v.isoformat()
        return json.dumps(data_to_sign, sort_keys=True).encode("utf-8")

    def verify_envelope(self, envelope: VeklomExecutionEnvelope) -> bool:
        """
        Mathematically validate the envelope.
        Raises FedcomVerificationError if validation fails.
        Returns True if successful.
        """
        # 1. Verify Time Constraints
        now = datetime.now(timezone.utc)
        if envelope.expires_at < now:
            raise EnvelopeExpiredError(f"Envelope expired at {envelope.expires_at.isoformat()}")
            
        # 2. Verify Cryptographic Signature
        canonical_data = self._canonicalize(envelope.model_dump())
        expected_sig = hmac.new(self._secret_key, canonical_data, hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(expected_sig, envelope.signature):
            raise SignatureVerificationError("Mathematical signature verification failed.")
            
        return True

