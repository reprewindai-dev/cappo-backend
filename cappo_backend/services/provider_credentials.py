"""Operator-side registration of a workspace's model provider credential.

``build_executor`` reads ``TenantProviderCredential`` rows to decide which model
providers a workspace can reach. This writes those rows: the endpoint is checked
with the same SSRF rules the executor applies, and any API key is sealed with the
vault master key under the associated data ``build_executor`` decrypts with.
There is deliberately no HTTP route for this; it is run by the operator on the host.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.models.tenant_provider_credential import TenantProviderCredential
from cappo_backend.security.ssrf import EndpointClass, validate_endpoint
from cappo_backend.security.vault import encrypt_secret


@dataclass(frozen=True)
class RegisteredCredential:
    id: str
    workspace_id: str
    provider: str
    credential_profile: str
    base_url: str | None
    key_version: int
    has_secret: bool


def register_provider_credential(
    db: Session,
    settings: Settings,
    *,
    workspace_id: str,
    provider: str,
    base_url: str | None = None,
    api_key: str | None = None,
    credential_profile: str = "default",
) -> RegisteredCredential:
    workspace_id = workspace_id.strip()
    provider = provider.strip().lower()
    if not workspace_id or not provider:
        raise ValueError("workspace_id and provider are required")
    base_url = (base_url or "").strip() or None
    if base_url:
        endpoint_class = (
            EndpointClass.TENANT_MANAGED_OLLAMA
            if provider == "ollama"
            else EndpointClass.EXTERNAL_PROVIDER
        )
        validate_endpoint(base_url, endpoint_class)
    elif provider == "ollama":
        raise ValueError("an ollama credential needs the base URL of that Ollama server")

    row = (
        db.query(TenantProviderCredential)
        .filter(
            TenantProviderCredential.workspace_id == workspace_id,
            TenantProviderCredential.provider == provider,
            TenantProviderCredential.credential_profile == credential_profile,
        )
        .one_or_none()
    )
    if row is None:
        row = TenantProviderCredential(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            provider=provider,
            credential_profile=credential_profile,
            key_version=1,
        )
        db.add(row)
    else:
        # A new version invalidates the old ciphertext's associated data.
        row.key_version = int(row.key_version or 1) + 1

    row.base_url = base_url
    row.disabled_at = None
    if api_key:
        associated_data = f"{workspace_id}:{provider}:{credential_profile}:{row.key_version}"
        row.encrypted_secret = encrypt_secret(settings.vault_master_key, api_key, associated_data)
        row.auth_type = "bearer"
    else:
        row.encrypted_secret = None
        row.auth_type = "none"
    db.commit()
    return RegisteredCredential(
        id=row.id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        credential_profile=row.credential_profile,
        base_url=row.base_url,
        key_version=row.key_version,
        has_secret=row.encrypted_secret is not None,
    )
