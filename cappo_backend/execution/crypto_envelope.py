import jwt
import time
import uuid
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

class CryptoEnvelope:
    """
    Handles Ed25519 cryptographic signing and verification for Veklom Execution Envelopes.
    Uses PyJWT for standard serialization, claims validation, and EdDSA support.
    """
    def __init__(self, private_key_bytes=None, public_key_bytes=None, kid="veklom-key-1"):
        self.kid = kid
        
        if private_key_bytes:
            self.private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
            self.public_key = self.private_key.public_key()
        elif public_key_bytes:
            self.private_key = None
            self.public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
        else:
            # Generate new keypair if none provided
            self.private_key = ed25519.Ed25519PrivateKey.generate()
            self.public_key = self.private_key.public_key()
            
    def get_public_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        
    def get_private_bytes(self) -> bytes:
        if not self.private_key:
            raise ValueError("No private key available")
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )

    def sign(self, payload: dict, audience: str) -> str:
        """
        Signs the envelope payload using EdDSA (Ed25519).
        Includes required claims like iss, aud, iat, nbf, exp, and jti (nonce).
        """
        if not self.private_key:
            raise ValueError("Cannot sign without a private key")
            
        now = int(time.time())
        claims = {
            "iss": "cappo.veklom.com",
            "aud": audience,
            "iat": now,
            "nbf": now,
            "exp": now + 300, # 5 minutes short expiry
            "jti": str(uuid.uuid4()) # Nonce
        }
        claims.update(payload)
        
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="EdDSA",
            headers={"kid": self.kid}
        )
        
    def verify(self, token: str, audience: str) -> dict:
        """
        Verifies the EdDSA signature and standard claims.
        Rejects modified, expired, or wrong-audience tokens.
        """
        return jwt.decode(
            token,
            self.public_key,
            algorithms=["EdDSA"],
            audience=audience,
            issuer="cappo.veklom.com"
        )
