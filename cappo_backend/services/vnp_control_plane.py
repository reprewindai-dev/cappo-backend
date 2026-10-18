"""VNP Control Plane Service — onboarding and registry management.

Handles registration of providers, APIs, and SDK credentials.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from cappo_backend.models.vnp_models import APIState, VNPProvider, VNPSDKCredential


class VNPControlPlaneService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def register_provider(self, name: str, commercial_profile: dict[str, Any] | None = None) -> VNPProvider:
        provider = VNPProvider(
            name=name,
            did=f"did:vnp:provider:{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:4]}",
            commercial_profile=commercial_profile or {}
        )
        self._db.add(provider)
        self._db.flush()
        return provider

    def register_api(
        self,
        provider_id: uuid.UUID,
        name: str,
        endpoint: str,
        version: str = "v1.0.0",
        x402_compliant: bool = False
    ) -> APIState:
        api = APIState(
            provider_id=provider_id,
            api_did=f"did:vnp:api:{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:4]}",
            name=name,
            endpoint=endpoint,
            version=version,
            x402_compliant=x402_compliant
        )
        self._db.add(api)
        self._db.flush()
        return api

    def create_sdk_credential(self, customer_id: uuid.UUID, entitlements: dict[str, Any] | None = None) -> VNPSDKCredential:
        cred = VNPSDKCredential(
            customer_id=customer_id,
            api_key=f"vnp_{uuid.uuid4().hex}",
            policy_entitlements=entitlements or {}
        )
        self._db.add(cred)
        self._db.flush()
        return cred
