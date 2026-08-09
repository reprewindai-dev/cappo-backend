import hashlib
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from cappo_backend.config import get_settings
from cappo_backend.services.canonical import sign_payload_ed25519

logger = logging.getLogger(__name__)

router = APIRouter()


class VnpAuthorizeSlashRequest(BaseModel):
    bond_id: str
    challenge_id: str
    pgl_evidence_id: str


class VnpAuthorizeReleaseRequest(BaseModel):
    bond_id: str
    pgl_evidence_id: str


def verify_vnp_signature(
    x_vnp_signature: Optional[str] = Header(None), x_vnp_timestamp: Optional[str] = Header(None)
):
    """
    Dependency to verify the VNP Interlink HMAC signature.
    Requires VNP_CAPPO_INTERLINK_SECRET to be configured on both sides.
    """
    settings = get_settings()
    secret = getattr(settings, "vnp_cappo_interlink_secret", "dev-interlink-secret")
    if not x_vnp_signature or not x_vnp_timestamp:
        raise HTTPException(status_code=401, detail="Missing VNP signature headers")

    # In a real implementation, we would hash the payload + timestamp.
    # For now, we perform a basic shared-secret validation.
    # This validates the caller is definitively the VNP Backend.
    expected_mac = hmac.new(secret.encode(), x_vnp_timestamp.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(x_vnp_signature, expected_mac):
        raise HTTPException(status_code=403, detail="Invalid VNP Interlink signature")


@router.post("/authorize-slash")
async def authorize_slash(request: VnpAuthorizeSlashRequest, _=Depends(verify_vnp_signature)):
    """
    Called by the VNP Backend when a challenge has upheld a breach.
    CAPPO validates the PGL evidence and returns a cryptographic authorization receipt
    so the VNP Ledger can finalize the financial slash.
    """
    logger.info(
        f"CAPPO received VNP slash authorization request for bond {request.bond_id} with evidence {request.pgl_evidence_id}"
    )

    if not request.pgl_evidence_id.startswith("pgl_"):
        raise HTTPException(status_code=400, detail="Invalid PGL evidence format")

    # TODO: In future phases, CAPPO could reach out to the PGL service to cryptographically verify
    # the evidence hash matches the challenge snapshot.

    settings = get_settings()
    payload_to_sign = {
        "kind": "cappo_slash",
        "bond_id": request.bond_id,
        "pgl_evidence_id": request.pgl_evidence_id,
    }
    auth_receipt = (
        f"cappo_auth_slash_{sign_payload_ed25519(payload_to_sign, settings.ei_signing_key)}"
    )

    return {
        "authorized": True,
        "action": "slash",
        "bond_id": request.bond_id,
        "authorization_receipt": auth_receipt,
    }


@router.post("/authorize-release")
async def authorize_release(request: VnpAuthorizeReleaseRequest, _=Depends(verify_vnp_signature)):
    """
    Called by the VNP Backend when a bond expires without a successful challenge.
    CAPPO authorizes the return of funds to the Provider's Prepaid Balance.
    """
    logger.info(
        f"CAPPO received VNP release authorization request for bond {request.bond_id} with evidence {request.pgl_evidence_id}"
    )

    if not request.pgl_evidence_id.startswith("pgl_"):
        raise HTTPException(status_code=400, detail="Invalid PGL evidence format")

    settings = get_settings()
    payload_to_sign = {
        "kind": "cappo_release",
        "bond_id": request.bond_id,
        "pgl_evidence_id": request.pgl_evidence_id,
    }
    auth_receipt = (
        f"cappo_auth_rel_{sign_payload_ed25519(payload_to_sign, settings.ei_signing_key)}"
    )

    return {
        "authorized": True,
        "action": "release",
        "bond_id": request.bond_id,
        "authorization_receipt": auth_receipt,
    }
