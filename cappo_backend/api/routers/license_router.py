"""License server endpoints — folded into cappo-backend.

This module makes cappo-backend the single license authority for Veklom.
Set LICENSE_SERVER_URL=https://cappo.veklom.com (or whatever domain cappo runs on)
in veklom-byos-backend so it validates here.

Routes:
    POST /v1/license/issue          — Issue a new license key (internal/admin)
    POST /v1/license/validate       — Validate a license key (called by byos-backend)
    POST /v1/license/activate       — Activate a license key for a workspace
    POST /v1/license/deactivate     — Deactivate / revoke a license key
    GET  /v1/license/{key}          — Get license metadata
    GET  /v1/license                — List all licenses (admin)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cappo_backend.config import get_settings
from cappo_backend.db.session import get_session
from cappo_backend.models.license_key import LicenseKey

router = APIRouter(prefix="/v1/license", tags=["License"])

settings = get_settings()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class IssueRequest(BaseModel):
    plan_tier: str = "starter"
    workspace_id: str | None = None
    expires_days: int | None = None  # None = no expiry
    max_activations: int = 1
    issued_by: str | None = None


class IssueResponse(BaseModel):
    key: str
    plan_tier: str
    status: str
    issued_at: str
    expires_at: str | None


class ValidateRequest(BaseModel):
    key: str
    workspace_id: str | None = None


class ValidateResponse(BaseModel):
    valid: bool
    status: str
    plan_tier: str | None = None
    workspace_id: str | None = None
    expires_at: str | None = None
    reason: str | None = None


class ActivateRequest(BaseModel):
    key: str
    workspace_id: str


class DeactivateRequest(BaseModel):
    key: str
    reason: str | None = None


# ---------------------------------------------------------------------------
# Auth helper — shared secret between byos-backend and cappo
# ---------------------------------------------------------------------------

def _verify_admin_token(x_license_admin_key: str = Header(default="")) -> None:
    """Verify the admin token for protected license endpoints.
    
    The veklom-byos-backend sends this as X-License-Admin-Key.
    Set LICENSE_ADMIN_KEY in both services' env vars.
    """
    expected = settings.license_admin_key
    if not expected:
        # No key configured — allow all (dev/test only)
        return
    if not hmac.compare_digest(x_license_admin_key, expected):
        raise HTTPException(status_code=403, detail="Invalid license admin key")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _generate_key(plan_tier: str) -> str:
    """Generate a licence key with a readable prefix and secure random suffix."""
    prefix_map = {
        "free": "VKL-FREE",
        "starter": "VKL-STRT",
        "pro": "VKL-STD",
        "sovereign": "VKL-SOV",
        "enterprise": "VKL-ENT",
    }
    prefix = prefix_map.get(plan_tier, "VKL")
    random_part = secrets.token_hex(16).upper()
    return f"{prefix}-{random_part[:8]}-{random_part[8:16]}-{random_part[16:24]}"


def _license_to_dict(lic: LicenseKey) -> dict[str, Any]:
    return {
        "id": lic.id,
        "key": lic.key,
        "plan_tier": lic.plan_tier,
        "status": lic.status,
        "workspace_id": lic.workspace_id,
        "issued_at": lic.issued_at.isoformat() if lic.issued_at else None,
        "activated_at": lic.activated_at.isoformat() if lic.activated_at else None,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        "revoked_at": lic.revoked_at.isoformat() if lic.revoked_at else None,
        "revoke_reason": lic.revoke_reason,
        "max_activations": lic.max_activations,
        "activation_count": lic.activation_count,
        "issued_by": lic.issued_by,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/issue", dependencies=[Depends(_verify_admin_token)])
def issue_license(body: IssueRequest, db: Session = Depends(get_session)) -> IssueResponse:
    """Issue a new license key. Called by admin/billing after a successful payment."""
    key = _generate_key(body.plan_tier)
    key_hash = _hash_key(key)

    expires_at = None
    if body.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)

    lic = LicenseKey(
        key=key,
        key_hash=key_hash,
        workspace_id=body.workspace_id,
        plan_tier=body.plan_tier,
        status="issued",
        expires_at=expires_at,
        max_activations=body.max_activations,
        issued_by=body.issued_by or "system",
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)

    return IssueResponse(
        key=lic.key,
        plan_tier=lic.plan_tier,
        status=lic.status,
        issued_at=lic.issued_at.isoformat(),
        expires_at=lic.expires_at.isoformat() if lic.expires_at else None,
    )


@router.post("/validate")
def validate_license(body: ValidateRequest, db: Session = Depends(get_session)) -> ValidateResponse:
    """Validate a license key. Called by veklom-byos-backend on every plan-gated request.

    The key itself is sent — we hash it server-side before lookup so plaintext
    keys are never stored in the database.
    """
    key_hash = _hash_key(body.key)
    lic = db.query(LicenseKey).filter(LicenseKey.key_hash == key_hash).first()

    if not lic:
        return ValidateResponse(valid=False, status="not_found", reason="License key not found")

    now = datetime.now(timezone.utc)

    if lic.status == "revoked":
        return ValidateResponse(
            valid=False, status="revoked",
            reason=lic.revoke_reason or "License has been revoked",
        )

    if lic.expires_at and now > lic.expires_at:
        # Auto-mark as expired
        lic.status = "expired"
        db.commit()
        return ValidateResponse(valid=False, status="expired", reason="License has expired")

    if lic.status not in ("active", "issued"):
        return ValidateResponse(valid=False, status=lic.status, reason=f"License status is {lic.status}")

    # Workspace binding check
    if lic.workspace_id and body.workspace_id and lic.workspace_id != body.workspace_id:
        return ValidateResponse(
            valid=False, status="workspace_mismatch",
            reason="License is bound to a different workspace",
        )

    return ValidateResponse(
        valid=True,
        status=lic.status,
        plan_tier=lic.plan_tier,
        workspace_id=lic.workspace_id,
        expires_at=lic.expires_at.isoformat() if lic.expires_at else None,
    )


@router.post("/activate")
def activate_license(body: ActivateRequest, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Activate a license key and bind it to a workspace."""
    key_hash = _hash_key(body.key)
    lic = db.query(LicenseKey).filter(LicenseKey.key_hash == key_hash).first()

    if not lic:
        raise HTTPException(status_code=404, detail="License key not found")
    if lic.status == "revoked":
        raise HTTPException(status_code=403, detail="License has been revoked")
    if lic.status == "expired":
        raise HTTPException(status_code=403, detail="License has expired")
    if lic.activation_count >= lic.max_activations:
        raise HTTPException(status_code=403, detail="Maximum activations reached")

    lic.workspace_id = body.workspace_id
    lic.status = "active"
    lic.activated_at = datetime.now(timezone.utc)
    lic.activation_count += 1
    db.commit()
    db.refresh(lic)

    return _license_to_dict(lic)


@router.post("/deactivate", dependencies=[Depends(_verify_admin_token)])
def deactivate_license(body: DeactivateRequest, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Revoke / deactivate a license key."""
    key_hash = _hash_key(body.key)
    lic = db.query(LicenseKey).filter(LicenseKey.key_hash == key_hash).first()

    if not lic:
        raise HTTPException(status_code=404, detail="License key not found")

    lic.status = "revoked"
    lic.revoked_at = datetime.now(timezone.utc)
    lic.revoke_reason = body.reason or "Deactivated by admin"
    db.commit()
    db.refresh(lic)

    return _license_to_dict(lic)


@router.get("/{key}", dependencies=[Depends(_verify_admin_token)])
def get_license(key: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Get license metadata by key value."""
    key_hash = _hash_key(key)
    lic = db.query(LicenseKey).filter(LicenseKey.key_hash == key_hash).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License key not found")
    return _license_to_dict(lic)


@router.get("", dependencies=[Depends(_verify_admin_token)])
def list_licenses(
    status: str | None = Query(default=None),
    plan_tier: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """List all license keys (admin only)."""
    q = db.query(LicenseKey)
    if status:
        q = q.filter(LicenseKey.status == status)
    if plan_tier:
        q = q.filter(LicenseKey.plan_tier == plan_tier)
    licenses = q.order_by(LicenseKey.issued_at.desc()).limit(limit).all()
    return [_license_to_dict(lic) for lic in licenses]
