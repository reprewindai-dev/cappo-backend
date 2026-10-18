"""Consequence permits: what a target presents to CAPPO before it commits an effect.

A permit is an HMAC over the operation, its mount, its authorization receipt, the
SHA-256 of the exact payload the target was sent, and the canonical ref of the
target CAPPO dispatched it to. It carries no authority by itself: the target must
redeem it with CAPPO, which decides against current mount state. A copied permit is
therefore useless once authority has ended, for any other payload, at any other
target, or after it has been redeemed once.

Target binding (v2). The target ref in the MAC comes from CAPPO's own registered
target configuration at dispatch, never from the caller. At redemption the target
signs its request with its own Ed25519 key; CAPPO verifies that signature against the
public key pinned for that target and recomputes the MAC with the *authenticated*
target's ref. A permit minted for target A therefore cannot be redeemed by target B,
even if B claims to be A.
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_DOMAIN = b"veklom-consequence-permit-v2"
_REDEEM_DOMAIN = "veklom-target-redeem-v1"


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
    key: bytes,
    *,
    operation_id: str,
    mount_id: str,
    receipt_id: str | None,
    payload_sha256: str,
    target_ref: str,
) -> str:
    if not target_ref:
        raise ValueError("a permit must be bound to a target")
    message = "\n".join([operation_id, mount_id, receipt_id or "", payload_sha256, target_ref]).encode()
    return hmac.new(key, _DOMAIN + b"\n" + message, hashlib.sha256).hexdigest()


def permit_matches(
    key: bytes,
    presented: str,
    *,
    operation_id: str,
    mount_id: str,
    receipt_id: str | None,
    payload_sha256: str,
    target_ref: str,
) -> bool:
    expected = mint_permit(
        key,
        operation_id=operation_id,
        mount_id=mount_id,
        receipt_id=receipt_id,
        payload_sha256=payload_sha256,
        target_ref=target_ref,
    )
    return hmac.compare_digest(expected, presented or "")


def redeem_message(*, operation_id: str, payload_sha256: str, permit: str, target_ref: str) -> bytes:
    """What a target signs when it redeems: binds its identity to this exact redemption."""
    permit_sha256 = hashlib.sha256((permit or "").encode()).hexdigest()
    return "\n".join([_REDEEM_DOMAIN, operation_id, payload_sha256, permit_sha256, target_ref]).encode()


def target_signature_valid(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(signature_hex or ""), message)
    except (ValueError, InvalidSignature):
        return False
    return True
