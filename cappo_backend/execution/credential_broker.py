import time
from typing import Optional

from cappo_backend.execution.crypto_envelope import CryptoEnvelope
from cappo_backend.execution.revocation_registry import LeaseState, RevocationRegistry


class BrokerException(Exception):
    pass

class CredentialBroker:
    """
    Veklom Credential Broker.
    Runs on the secure CAPPO boundary. Never exposes keys to n8n.
    Resolves opaque credential references to actual secrets for governed targets.
    """
    def __init__(self, crypto: CryptoEnvelope, revocations: RevocationRegistry, vault: dict):
        self.crypto = crypto
        self.revocations = revocations
        self.vault = vault # Memory vault for demonstration
        self.resolution_tracker = {} # Tracks execution_id to prevent duplicate resolution

    def resolve(self, authority_token: str, credential_ref: str, target_audience: str, requested_scope: str) -> str:
        # 1. Crypto Verification
        try:
            authority = self.crypto.verify(authority_token, audience=target_audience)
        except Exception as e:
            raise BrokerException(f"DENY: Invalid authority token. {e}")
            
        jti = authority.get("jti")
        execution_id = authority.get("execution_id")
        lease_id = authority.get("lease_id")
        sub = authority.get("sub")
        allowed_resources = authority.get("allowed_resources", [])
        
        import jwt
        unverified_headers = jwt.get_unverified_header(authority_token)
        kid = unverified_headers.get("kid")
        
        # 2. Check live authority state
        state = self.revocations.check_authority(kid, sub, lease_id, execution_id)
        if state in (LeaseState.REVOKED, LeaseState.CANCELLING):
            raise BrokerException("DENY: Authority revoked")
            
        # 3. Scope validation
        if requested_scope not in allowed_resources and "*" not in allowed_resources:
            raise BrokerException(f"DENY: Scope '{requested_scope}' not authorized in lease.")
            
        # 4. Bind credential to exactly one execution (One-time resolution logic)
        # In a real broker, this might issue a time-bound STS token. We simulate bounding it.
        # If an execution tries to resolve the same ref twice, or broader authority, we check.
        tracker_key = f"{execution_id}:{credential_ref}"
        if tracker_key in self.resolution_tracker:
            prev_scope = self.resolution_tracker[tracker_key]
            if prev_scope != requested_scope:
                raise BrokerException("DENY: Duplicate execution cannot obtain broader authority.")
            # Depending on policy, we might allow multiple fetches within the same execution
            # Or strict one-time:
            raise BrokerException("DENY: Credential reference already resolved for this execution.")
            
        self.resolution_tracker[tracker_key] = requested_scope
        
        # 5. Fetch from Vault
        secret = self.vault.get(credential_ref)
        if not secret:
            raise BrokerException("DENY: Invalid credential reference")
            
        return secret
