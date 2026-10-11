"""VRE-0 contract objects: canonical encoding, digests, Ed25519 signatures.

Four records make up one execution, each committing to the one before it:

  envelope  E  signed by the issuer (authority): what may be materialized
  boundary  B  what the supervisor actually configured, read back from the
               kernel, plus the process that was placed in it; commits to E
  evidence  X  substrate observations from release to teardown; commits to B
  receipt   R  signed by the executor: outcome + digests of E, B and X

All values are integers or strings (no floats), so the canonical JSON is
byte-stable across machines.
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def public_key_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def key_id(public_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(public_b64)).hexdigest()[:16]


def sign(payload: dict, key: Ed25519PrivateKey) -> dict:
    pub = public_key_b64(key)
    return {
        "alg": "Ed25519",
        "key_id": key_id(pub),
        "payload": payload,
        "sig": base64.b64encode(key.sign(canonical(payload))).decode(),
    }


def verify(signed: dict, trusted: dict[str, str]) -> bool:
    """trusted maps key_id -> base64 raw public key."""
    pub = trusted.get(signed.get("key_id", ""))
    if pub is None or signed.get("alg") != "Ed25519":
        return False
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(
            base64.b64decode(signed["sig"]), canonical(signed["payload"]))
        return True
    except Exception:
        return False
