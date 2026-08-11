from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.vnp_interlink_nonce import VNPInterlinkNonce

router = APIRouter()
VNP_SIGNATURE_MAX_AGE_SECONDS = 300


class VnpAuthorizeSlashRequest(BaseModel):
    bond_id: str
    challenge_id: str
    pgl_evidence_id: str


class VnpAuthorizeReleaseRequest(BaseModel):
    bond_id: str
    pgl_evidence_id: str


def _parse_timestamp(value: str) -> datetime:
    try:
        if value.replace(".", "", 1).isdigit():
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(status_code=401, detail="Invalid VNP timestamp") from exc


def _canonical_json(raw: bytes) -> str:
    try:
        value = json.loads(raw or b"{}")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def verify_vnp_signature(
    request: Request,
    x_vnp_signature: Optional[str] = Header(None),
    x_vnp_timestamp: Optional[str] = Header(None),
    x_vnp_nonce: Optional[str] = Header(None),
    db: Session = Depends(get_session),
) -> None:
    """Verify a body-bound, expiring, replay-resistant VNP Interlink request."""
    if not x_vnp_signature or not x_vnp_timestamp or not x_vnp_nonce:
        raise HTTPException(status_code=401, detail="Missing VNP signature headers")
    if len(x_vnp_nonce) < 16 or len(x_vnp_nonce) > 255:
        raise HTTPException(status_code=401, detail="Invalid VNP nonce")

    secret = os.getenv("VNP_CAPPO_INTERLINK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="VNP Interlink verification unavailable")

    timestamp = _parse_timestamp(x_vnp_timestamp)
    now = datetime.now(timezone.utc)
    if abs((now - timestamp).total_seconds()) > VNP_SIGNATURE_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Expired VNP signature")

    canonical_body = _canonical_json(await request.body())
    signed_message = "\n".join(
        [
            request.method.upper(),
            request.url.path,
            x_vnp_timestamp,
            x_vnp_nonce,
            canonical_body,
        ]
    )
    expected_mac = hmac.new(
        secret.encode("utf-8"),
        signed_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(x_vnp_signature, expected_mac):
        raise HTTPException(status_code=403, detail="Invalid VNP Interlink signature")

    expires_at = now + timedelta(seconds=VNP_SIGNATURE_MAX_AGE_SECONDS)
    try:
        db.execute(delete(VNPInterlinkNonce).where(VNPInterlinkNonce.expires_at <= now))
        db.add(VNPInterlinkNonce(nonce=x_vnp_nonce, expires_at=expires_at))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="VNP request replay detected") from exc


def _unverified_evidence_response(*, action: str, bond_id: str) -> JSONResponse:
    """Deny financial authorization until canonical PGL evidence verification exists.

    Request authentication proves who called CAPPO; it does not prove that the
    referenced durable evidence exists or is bound to this bond/challenge/action.
    CAPPO therefore must not mint an authorization receipt from identifier syntax.
    """
    return JSONResponse(
        status_code=503,
        content={
            "authorized": False,
            "action": action,
            "bond_id": bond_id,
            "evidence_status": "NOT_VERIFIED",
            "reason": "canonical_pgl_evidence_verifier_unavailable",
        },
    )


@router.post("/authorize-slash")
async def authorize_slash(
    request: VnpAuthorizeSlashRequest,
    _: None = Depends(verify_vnp_signature),
):
    if not request.pgl_evidence_id.startswith("pgl_"):
        raise HTTPException(status_code=400, detail="Invalid PGL evidence format")

    # Fail closed. A pgl_* identifier is not proof that the evidence exists, is
    # authentic, or is bound to this bond/challenge/slash action. No CAPPO-signed
    # authorization receipt is issued until a canonical durable verifier exists.
    return _unverified_evidence_response(action="slash", bond_id=request.bond_id)


@router.post("/authorize-release")
async def authorize_release(
    request: VnpAuthorizeReleaseRequest,
    _: None = Depends(verify_vnp_signature),
):
    if not request.pgl_evidence_id.startswith("pgl_"):
        raise HTTPException(status_code=400, detail="Invalid PGL evidence format")

    return _unverified_evidence_response(action="release", bond_id=request.bond_id)
