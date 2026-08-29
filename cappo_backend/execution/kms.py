import jwt
import time
import uuid
import json
import os
import stat
from typing import Dict, Optional
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from sqlalchemy.orm import Session
from cappo_backend.db.session import SessionLocal
from cappo_backend.models.kms_models import KMSKeyRecord, KMSKeyStatus

# Private keys must live completely outside of version control, synchronized folders, or evidence bundles.
HSM_MOCK_STORAGE_FILE = os.path.expanduser("~/.cappo_mock_kms_keys.json")

class MockHardwareSecurityModule:
    """
    Mocks an external KMS/HSM service (like AWS KMS or HashiCorp Vault).
    The private keys NEVER leave this module and are NOT stored in the PostgreSQL database.
    """
    @staticmethod
    def _enforce_permissions():
        if os.path.exists(HSM_MOCK_STORAGE_FILE):
            os.chmod(HSM_MOCK_STORAGE_FILE, stat.S_IRUSR | stat.S_IWUSR)

    @staticmethod
    def _load_keys() -> dict:
        if not os.path.exists(HSM_MOCK_STORAGE_FILE):
            return {}
        MockHardwareSecurityModule._enforce_permissions()
        with open(HSM_MOCK_STORAGE_FILE, 'r') as f:
            return json.load(f)

    @staticmethod
    def _save_keys(keys_data: dict):
        with open(HSM_MOCK_STORAGE_FILE, 'w') as f:
            json.dump(keys_data, f)
        MockHardwareSecurityModule._enforce_permissions()
            
    @classmethod
    def generate_key_in_enclave(cls, kid: str) -> bytes:
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        
        priv_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        pub_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        
        keys = cls._load_keys()
        keys[kid] = priv_bytes.hex()
        cls._save_keys(keys)
        
        return pub_bytes

    @classmethod
    def sign_with_enclave(cls, kid: str, payload: dict, audience: str) -> str:
        keys = cls._load_keys()
        if kid not in keys:
            raise ValueError(f"Mock KMS/HSM Error: Key {kid} not found in hardware boundary")
            
        private_bytes = bytes.fromhex(keys[kid])
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
        
        now = int(time.time())
        claims = {
            "iss": "cappo.veklom.com",
            "aud": audience,
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4())
        }
        claims.update(payload)
        
        return jwt.encode(
            claims,
            private_key,
            algorithm="EdDSA",
            headers={"kid": kid}
        )


class LocalKMSProvider:
    """
    An HSM/KMS abstraction for managed key custody, backed by PostgreSQL for metadata
    and an isolated Mock HSM for private key operations.
    """
    def __init__(self):
        pass
        
    def generate_new_key(self) -> str:
        kid = f"key-{uuid.uuid4().hex[:8]}"
        
        # 1. Ask the HSM to generate and retain the private key, returning only public bytes.
        public_bytes = MockHardwareSecurityModule.generate_key_in_enclave(kid)
        now = time.time()
        
        # 2. Store only the public key metadata in PostgreSQL
        with SessionLocal() as db:
            active_keys = db.query(KMSKeyRecord).filter(KMSKeyRecord.status == KMSKeyStatus.ACTIVE).all()
            for key in active_keys:
                key.status = KMSKeyStatus.RETIRED
                key.expires_at = now + 3600
            
            record = KMSKeyRecord(
                kid=kid,
                public_bytes=public_bytes,
                private_bytes=None, # Explicitly dropping DB custody of private keys
                status=KMSKeyStatus.ACTIVE,
                created_at=now
            )
            db.add(record)
            db.commit()
            
        return kid
        
    def revoke_key(self, kid: str):
        with SessionLocal() as db:
            record = db.query(KMSKeyRecord).filter(KMSKeyRecord.kid == kid).first()
            if record:
                record.status = KMSKeyStatus.REVOKED
                db.commit()
            
    def get_public_key(self, kid: str) -> Optional[bytes]:
        with SessionLocal() as db:
            record = db.query(KMSKeyRecord).filter(KMSKeyRecord.kid == kid).first()
            if not record:
                return None
            if record.status == KMSKeyStatus.REVOKED:
                return None
            if record.status == KMSKeyStatus.RETIRED and record.expires_at and time.time() > record.expires_at:
                return None
            return record.public_bytes
            
    def sign(self, payload: dict, audience: str) -> str:
        with SessionLocal() as db:
            record = db.query(KMSKeyRecord).filter(KMSKeyRecord.status == KMSKeyStatus.ACTIVE).first()
            if not record:
                raise ValueError("No active key available for signing")
            active_kid = record.kid
            
        # Private key never leaves the HSM mock; we ask HSM to sign it for us.
        return MockHardwareSecurityModule.sign_with_enclave(active_kid, payload, audience)


class GovernedTargetVerifier:
    def __init__(self, key_fetcher_callable):
        self.fetch_public_key = key_fetcher_callable
        
    def verify(self, token: str, audience: str) -> dict:
        try:
            headers = jwt.get_unverified_header(token)
        except Exception:
            raise ValueError("Invalid JWT format")
            
        kid = headers.get("kid")
        if not kid:
            raise ValueError("Missing 'kid' in header")
            
        public_bytes = self.fetch_public_key(kid)
        if not public_bytes:
            raise ValueError(f"Key {kid} not found, expired, or revoked")
            
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)
        
        return jwt.decode(
            token,
            public_key,
            algorithms=["EdDSA"],
            audience=audience,
            issuer="cappo.veklom.com"
        )
