"""Consequence permits: what a target presents to CAPPO before it commits an effect.

A permit is an HMAC over the operation, its mount, its authorization receipt and the
SHA-256 of the exact payload the target was sent. It carries no authority by itself:
the target must redeem it with CAPPO, which decides against current mount state. A
copied permit is therefore useless once authority has ended, for any other payload,
or after it has been redeemed once.
"""

from __future__ import annotations

import hashlib
import hmac

_DOMAIN = b"veklom-consequence-permit-v1"


def permit_key(settings: object | None) -> bytes | None:
    """Key for consequence permits, or None when permits are not configured.

    CONSEQUENCE_PERMIT_KEY when set; otherwise derived (domain-separated) from the
    approval-token signing key that production already requires.
    """
    explicit = getattr(settings, "consequence_permit_key", "") or ""
    if explicit:
        return hashlib.sha256(_DOMAIN + b"|explicit|" + explicit.encode()).digest()
    base = getattr(settings, "approval_token_signing_key", "") or ""
    if base:
        return hmac.new(base.encode(), _DOMAIN + b"|derived", hashlib.sha256).digest()
    return None


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def mint_permit(
    key: bytes, *, operation_id: str, mount_id: str, receipt_id: str | None, payload_sha256: str
) -> str:
    message = "\n".join([operation_id, mount_id, receipt_id or "", payload_sha256]).encode()
    return hmac.new(key, _DOMAIN + b"\n" + message, hashlib.sha256).hexdigest()


def permit_matches(
    key: bytes,
    presented: str,
    *,
    operation_id: str,
    mount_id: str,
    receipt_id: str | None,
    payload_sha256: str,
) -> bool:
    expected = mint_permit(
        key,
        operation_id=operation_id,
        mount_id=mount_id,
        receipt_id=receipt_id,
        payload_sha256=payload_sha256,
    )
    return hmac.compare_digest(expected, presented or "")
