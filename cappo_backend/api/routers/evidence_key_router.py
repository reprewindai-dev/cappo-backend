"""Public key that verifies CAPPO's signed execution receipts (COSE_Sign1, Ed25519).

Anyone holding a receipt can check it against this key without an account and without
trusting CAPPO's own verdict. If the key is unavailable the route says so with 503; it
never invents one.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cappo_backend.security.evidence import EvidenceKeyUnavailable, evidence_issuer, evidence_public_key_descriptor

evidence_key_router = APIRouter()


@evidence_key_router.get("/.well-known/evidence-keys")
@evidence_key_router.get("/.well-known/evidence-keys.json", include_in_schema=False)
def get_evidence_keys() -> JSONResponse:
    try:
        key = evidence_public_key_descriptor()
    except EvidenceKeyUnavailable as exc:
        return JSONResponse(
            status_code=503,
            content={"issuer": evidence_issuer(), "keys": [], "status": "EVIDENCE_KEY_UNAVAILABLE", "detail": str(exc)},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        content={
            "issuer": evidence_issuer(),
            "format": "COSE_Sign1 (RFC 9052), alg EdDSA (-8), kid in unprotected header label 4",
            "keys": [key],
        },
        headers={"Cache-Control": "public, max-age=300, must-revalidate"},
    )
