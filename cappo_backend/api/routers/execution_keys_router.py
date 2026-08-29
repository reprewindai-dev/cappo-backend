"""Public verification-key discovery for governed execution targets."""

import base64

from fastapi import APIRouter, HTTPException

from cappo_backend.execution.kms import LocalKMSProvider

router = APIRouter(prefix="/api/v1/execution/keys", tags=["execution-keys"])


@router.get("/{kid}")
def get_execution_verification_key(kid: str) -> dict[str, str]:
    public_key = LocalKMSProvider().get_public_key(kid)
    if public_key is None:
        raise HTTPException(status_code=404, detail="Key not found, expired, or revoked")
    return {
        "kid": kid,
        "algorithm": "EdDSA",
        "public_key": base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("="),
    }


__all__ = ["router"]
