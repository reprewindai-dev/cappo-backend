"""Canonical serialization, hashing, and signing helpers.

Lineage seed: the old backend's ``_sha256_json()`` provenance helper and the
``AIAuditLog`` HMAC-SHA256 ``log_hash`` pattern. CAPPO promotes these to a shared,
deterministic primitive used by PGL certificates, the ledger, and
ExecutionIdentityV1.

Determinism rule: the same logical payload must always produce the same hash and
signature. We therefore serialize with sorted keys, no insignificant whitespace,
and ``ensure_ascii=False`` so equivalent unicode is stable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Serialize ``payload`` to a canonical, deterministic JSON string."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def sha256_json(payload: Any) -> str:
    """Return the hex SHA-256 of the canonical JSON of ``payload``."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sign_payload(payload: Any, signing_key: str) -> str:
    """Return a hex HMAC-SHA256 signature over the canonical JSON of ``payload``."""
    return hmac.new(
        signing_key.encode("utf-8"),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(payload: Any, signature: str, signing_key: str) -> bool:
    """Constant-time verification of a signature produced by :func:`sign_payload`."""
    expected = sign_payload(payload, signing_key)
    return hmac.compare_digest(expected, signature)
