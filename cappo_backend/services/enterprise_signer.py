"""Enterprise-grade signing adapters for ExecutionIdentityV1.

Implements HSM-backed asymmetric signing using AWS KMS, Azure Key Vault,
or HashiCorp Vault. These signers provide non-repudiation, hardware key
protection, and audit logging required for enterprise compliance.

Usage:
    from cappo_backend.services.enterprise_signer import KMSSigner
    signer = KMSSigner(key_id="arn:aws:kms:...")
    signature = signer.sign(payload)

All signers implement the :class:`Signer` protocol from ei_builder.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from cappo_backend.services.canonical import canonical_json

if TYPE_CHECKING:
    pass


class SigningError(Exception):
    """Raised when enterprise signing fails (HSM unavailable, permission denied, etc.)."""


class KeyNotFoundError(SigningError):
    """Raised when the specified KMS key cannot be found or accessed."""


@dataclass
class KMSSigner:
    """AWS KMS-backed ECDSA P-256 signer.
    
    The private key never leaves the HSM. AWS KMS performs the signing
    operation internally and returns the signature. Supports key policies,
    rotation, and CloudTrail audit logging.
    
    Attributes:
        key_id: AWS KMS key ARN or alias (e.g., "arn:aws:kms:us-east-1:123:key/abc")
        region: AWS region (defaults to environment/boto3 config)
        algorithm: Signing algorithm (default: ECDSA_SHA_256)
    
    Example:
        signer = KMSSigner(key_id="arn:aws:kms:us-east-1:123456789:key/12345678")
        signature = signer.sign({"execution_id": "...", ...})
    """
    
    key_id: str
    region: str | None = None
    algorithm: Literal["ECDSA_SHA_256", "ECDSA_SHA_384", "ECDSA_SHA_512", "RSASSA_PKCS1_V1_5_SHA_256"] = "ECDSA_SHA_256"
    
    def __post_init__(self):
        self._client = None
        self._public_key: bytes | None = None
    
    def _get_client(self) -> Any:
        """Lazy initialization of boto3 KMS client."""
        if self._client is None:
            try:
                import boto3
                kwargs = {}
                if self.region:
                    kwargs["region_name"] = self.region
                self._client = boto3.client("kms", **kwargs)
            except ImportError:
                raise SigningError("boto3 is required for AWS KMS signing. Install: pip install boto3")
            except Exception as e:
                raise SigningError(f"Failed to initialize AWS KMS client: {e}")
        return self._client
    
    def _get_public_key(self) -> bytes:
        """Fetch and cache the public key for local verification."""
        if self._public_key is None:
            client = self._get_client()
            try:
                response = client.get_public_key(KeyId=self.key_id)
                self._public_key = response["PublicKey"]
            except client.exceptions.NotFoundException:
                raise KeyNotFoundError(f"KMS key not found: {self.key_id}")
            except Exception as e:
                raise SigningError(f"Failed to fetch KMS public key: {e}")
        return self._public_key
    
    def sign(self, payload: Any) -> str:
        """Sign the canonical JSON of payload using AWS KMS.
        
        The signing operation is performed inside the HSM. The private key
        never leaves AWS infrastructure.
        
        Returns:
            Base64-encoded DER-encoded ECDSA signature.
            
        Raises:
            SigningError: If KMS signing fails.
            KeyNotFoundError: If the key doesn't exist or isn't accessible.
        """
        client = self._get_client()
        message = canonical_json(payload).encode("utf-8")
        
        try:
            response = client.sign(
                KeyId=self.key_id,
                Message=message,
                MessageType="RAW",
                SigningAlgorithm=self.algorithm,
            )
            signature = base64.b64encode(response["Signature"]).decode("ascii")
            return signature
        except client.exceptions.NotFoundException:
            raise KeyNotFoundError(f"KMS key not found: {self.key_id}")
        except client.exceptions.DisabledException:
            raise SigningError(f"KMS key is disabled: {self.key_id}")
        except client.exceptions.InvalidKeyUsageException:
            raise SigningError(f"KMS key not configured for signing: {self.key_id}")
        except Exception as e:
            raise SigningError(f"AWS KMS signing failed: {e}")
    
    def verify(self, payload: Any, signature: str) -> bool:
        """Verify signature using the KMS public key (local verification).
        
        Local verification is faster than calling KMS Verify API and
        doesn't require additional KMS permissions.
        
        Args:
            payload: The original payload that was signed.
            signature: Base64-encoded signature from sign().
            
        Returns:
            True if signature is valid, False otherwise.
        """
        try:
            import cryptography.hazmat.primitives.asymmetric.ec as ec
            import cryptography.hazmat.primitives.hashes as hashes
            import cryptography.hazmat.primitives.serialization as serialization
            from cryptography.exceptions import InvalidSignature
            
            public_key = self._get_public_key()
            
            # Load the public key
            key = serialization.load_der_public_key(public_key)
            if not isinstance(key, ec.EllipticCurvePublicKey):
                raise SigningError("KMS key is not an ECDSA key")
            
            # Decode signature
            sig_bytes = base64.b64decode(signature)
            message = canonical_json(payload).encode("utf-8")
            
            # Verify
            key.verify(sig_bytes, message, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False
        except ImportError:
            raise SigningError("cryptography library required for local verification. Install: pip install cryptography")
        except Exception as e:
            raise SigningError(f"Signature verification failed: {e}")


@dataclass
class AzureKeyVaultSigner:
    """Azure Key Vault ECDSA/ECDH signer.
    
    Uses Azure Key Vault's KeyClient and CryptographyClient for HSM-backed
    signing. Supports Managed Identity, Service Principals, or Azure CLI auth.
    
    Attributes:
        key_url: Full Azure Key Vault key URL (e.g., "https://myvault.vault.azure.net/keys/mykey")
        credential: Optional Azure credential (defaults to DefaultAzureCredential)
    """
    
    key_url: str
    credential: Any = None
    
    def __post_init__(self):
        self._key_client = None
        self._crypto_client = None
    
    def _get_clients(self) -> tuple[Any, Any]:
        """Lazy initialization of Azure Key Vault clients."""
        if self._crypto_client is None:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.keyvault.keys import KeyClient
                from azure.keyvault.keys.crypto import CryptographyClient, SignatureAlgorithm
                
                credential = self.credential or DefaultAzureCredential()
                vault_url = self.key_url.rsplit("/keys/", 1)[0]
                
                self._key_client = KeyClient(vault_url=vault_url, credential=credential)
                key = self._key_client.get_key(self.key_url.split("/")[-1])
                self._crypto_client = CryptographyClient(key, credential=credential)
                self._algorithm = SignatureAlgorithm.es256  # ECDSA with P-256 and SHA-256
                
            except ImportError:
                raise SigningError("azure-keyvault-keys and azure-identity required. Install: pip install azure-keyvault-keys azure-identity")
            except Exception as e:
                raise SigningError(f"Failed to initialize Azure Key Vault client: {e}")
        
        return self._key_client, self._crypto_client
    
    def sign(self, payload: Any) -> str:
        """Sign using Azure Key Vault HSM."""
        _, crypto_client = self._get_clients()
        message = canonical_json(payload).encode("utf-8")
        
        try:
            from azure.keyvault.keys.crypto import SignatureAlgorithm
            
            digest = hashlib.sha256(message).digest()
            result = crypto_client.sign(SignatureAlgorithm.es256, digest)
            signature = base64.b64encode(result.signature).decode("ascii")
            return signature
        except Exception as e:
            raise SigningError(f"Azure Key Vault signing failed: {e}")
    
    def verify(self, payload: Any, signature: str) -> bool:
        """Verify using Azure Key Vault (remote verification)."""
        _, crypto_client = self._get_clients()
        message = canonical_json(payload).encode("utf-8")
        
        try:
            from azure.keyvault.keys.crypto import SignatureAlgorithm
            
            digest = hashlib.sha256(message).digest()
            sig_bytes = base64.b64decode(signature)
            result = crypto_client.verify(SignatureAlgorithm.es256, digest, sig_bytes)
            return result.is_valid
        except Exception as e:
            raise SigningError(f"Azure Key Vault verification failed: {e}")


@dataclass
class HashiCorpVaultSigner:
    """HashiCorp Vault Transit secrets engine signer.
    
    Uses Vault's Transit engine for HSM-backed signing. Supports AppRole,
    Kubernetes auth, token auth, and other Vault auth methods.
    
    Attributes:
        vault_url: Vault server URL (e.g., "https://vault.example.com:8200")
        key_name: Transit key name (e.g., "cappo-ei-signing-key")
        mount_point: Transit secrets engine mount (default: "transit")
        auth_method: Auth method to use (token, approle, kubernetes, etc.)
    """
    
    vault_url: str
    key_name: str
    mount_point: str = "transit"
    auth_method: Literal["token", "approle", "kubernetes", "userpass"] = "token"
    token: str | None = None
    role_id: str | None = None
    secret_id: str | None = None
    
    def __post_init__(self):
        self._client = None
    
    def _get_client(self) -> Any:
        """Lazy initialization of hvac Vault client."""
        if self._client is None:
            try:
                import hvac
                
                self._client = hvac.Client(url=self.vault_url)
                
                if self.auth_method == "token":
                    if not self.token:
                        raise SigningError("Vault token required for token auth")
                    self._client.token = self.token
                elif self.auth_method == "approle":
                    if not (self.role_id and self.secret_id):
                        raise SigningError("role_id and secret_id required for AppRole auth")
                    self._client.auth.approle.login(role_id=self.role_id, secret_id=self.secret_id)
                elif self.auth_method == "kubernetes":
                    self._client.auth.kubernetes.login(role=self.key_name)
                else:
                    raise SigningError(f"Unsupported Vault auth method: {self.auth_method}")
                    
            except ImportError:
                raise SigningError("hvac required for Vault signing. Install: pip install hvac")
            except Exception as e:
                raise SigningError(f"Failed to initialize Vault client: {e}")
        
        return self._client
    
    def sign(self, payload: Any) -> str:
        """Sign using Vault Transit secrets engine."""
        client = self._get_client()
        message = canonical_json(payload)
        
        try:
            import hvac
            
            result = client.secrets.transit.sign_data(
                name=self.key_name,
                mount_point=self.mount_point,
                hash_input=base64.b64encode(message.encode("utf-8")).decode("ascii"),
                hash_algorithm="sha2-256",
                signature_algorithm="pss",  # RSA-PSS or "pkcs1v15"
                marshaling_algorithm="asn1",
            )
            signature = result["data"]["signature"]
            # Vault returns "vault:v1:base64signature", strip prefix
            if signature.startswith("vault:v1:"):
                signature = signature[9:]
            return signature
        except hvac.exceptions.VaultError as e:
            raise SigningError(f"Vault signing failed: {e}")
        except Exception as e:
            raise SigningError(f"Vault signing failed: {e}")
    
    def verify(self, payload: Any, signature: str) -> bool:
        """Verify using Vault Transit secrets engine (remote verification)."""
        client = self._get_client()
        message = canonical_json(payload)
        
        try:
            
            # Re-add vault prefix if missing
            if not signature.startswith("vault:v"):
                signature = f"vault:v1:{signature}"
            
            result = client.secrets.transit.verify_signed_data(
                name=self.key_name,
                mount_point=self.mount_point,
                hash_input=base64.b64encode(message.encode("utf-8")).decode("ascii"),
                signature=signature,
                hash_algorithm="sha2-256",
                signature_algorithm="pss",
                marshaling_algorithm="asn1",
            )
            return result["data"]["valid"]
        except Exception as e:
            raise SigningError(f"Vault verification failed: {e}")


def create_enterprise_signer(
    provider: Literal["aws", "azure", "vault", "hmac"],
    **kwargs
) -> Any:
    """Factory function to create appropriate signer from configuration.
    
    Usage:
        signer = create_enterprise_signer(
            provider="aws",
            key_id="arn:aws:kms:us-east-1:123456789:key/12345678-1234-1234-1234-123456789012"
        )
        
        # Or for dev/staging (HMAC):
        signer = create_enterprise_signer(
            provider="hmac",
            signing_key="dev-key-123"
        )
    
    Args:
        provider: One of "aws" (KMS), "azure" (Key Vault), "vault" (HashiCorp), or "hmac"
        **kwargs: Provider-specific configuration
        
    Returns:
        Signer instance implementing the Signer protocol.
    """
    if provider == "aws":
        return KMSSigner(**kwargs)
    elif provider == "azure":
        return AzureKeyVaultSigner(**kwargs)
    elif provider == "vault":
        return HashiCorpVaultSigner(**kwargs)
    elif provider == "hmac":
        from cappo_backend.services.ei_builder import Ed25519Signer
        return Ed25519Signer(**kwargs)
    else:
        raise ValueError(f"Unknown signer provider: {provider}")


def create_enterprise_signer_from_settings(settings: Any) -> Any:
    """Create signer from CAPPO Settings configuration.
    
    This is the production entry point. Reads EI_SIGNING_PROVIDER and
    provider-specific settings from the Settings object.
    
    Args:
        settings: CAPPO Settings instance (from cappo_backend.config.Settings)
        
    Returns:
        Signer instance ready for production use.
        
    Example:
        from cappo_backend.config import get_settings
        from cappo_backend.services.enterprise_signer import create_enterprise_signer_from_settings
        
        settings = get_settings()
        signer = create_enterprise_signer_from_settings(settings)
    """
    provider = settings.ei_signing_provider.lower()
    
    if provider == "aws":
        if not settings.aws_kms_key_id:
            raise SigningError("AWS_KMS_KEY_ID not configured")
        return KMSSigner(
            key_id=settings.aws_kms_key_id,
            region=settings.aws_region,
        )
    elif provider == "azure":
        if not settings.azure_key_vault_url:
            raise SigningError("AZURE_KEY_VAULT_URL not configured")
        return AzureKeyVaultSigner(
            key_url=settings.azure_key_vault_url,
        )
    elif provider == "vault":
        if not settings.vault_url or not settings.vault_transit_key_name:
            raise SigningError("VAULT_URL and VAULT_TRANSIT_KEY_NAME not configured")
        return HashiCorpVaultSigner(
            vault_url=settings.vault_url,
            key_name=settings.vault_transit_key_name,
            token=settings.vault_token,
        )
    elif provider == "hmac":
        from cappo_backend.services.ei_builder import Ed25519Signer
        return Ed25519Signer(signing_key=settings.ei_signing_key)
    else:
        raise SigningError(f"Unknown EI_SIGNING_PROVIDER: {provider}")
