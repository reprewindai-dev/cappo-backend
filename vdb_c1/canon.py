"""Canonical encoding, digests, Ed25519 signing, state roots, time."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

STATE_PROTOCOL = "veklom.vdb.c1.state/v1"
PAYLOAD_PROTOCOL = "veklom.vdb.c1.payload/v1"


def jcs(obj) -> bytes:
    return rfc8785.dumps(obj)


def digest(obj) -> str:
    return "sha256:" + hashlib.sha256(jcs(obj)).hexdigest()


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    """Strict base64url without padding; non-canonical encodings are refused."""
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64u(raw) != text:
        raise ValueError("non-canonical base64url")
    return raw


def key_id(pub_raw: bytes) -> str:
    return "ed25519:" + hashlib.sha256(pub_raw).hexdigest()[:16]


def public_raw(sk: Ed25519PrivateKey) -> bytes:
    return sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def generate_key() -> tuple[Ed25519PrivateKey, str, str]:
    sk = Ed25519PrivateKey.generate()
    pub = public_raw(sk)
    return sk, key_id(pub), b64u(pub)


def sign(payload: dict, sk: Ed25519PrivateKey, kid: str) -> dict:
    return {"alg": "Ed25519", "kid": kid, "payload": payload, "sig": b64u(sk.sign(jcs(payload)))}


def verify_signed(signed: dict, trusted: dict[str, str]) -> bool:
    """trusted maps kid -> base64url raw public key."""
    try:
        pub = unb64u(trusted[signed["kid"]])
        if signed["alg"] != "Ed25519" or key_id(pub) != signed["kid"]:
            return False
        Ed25519PublicKey.from_public_bytes(pub).verify(unb64u(signed["sig"]), jcs(signed["payload"]))
        return True
    except Exception:
        return False


def state_root(namespace: str, generation: int, entries: list[list[str]]) -> str:
    return digest({"protocol": STATE_PROTOCOL, "namespace": namespace,
                   "generation": generation, "entries": sorted(entries)})


def payload_commitment(op: str, key: str, value: str) -> str:
    return digest({"protocol": PAYLOAD_PROTOCOL, "op": op, "key": key, "value": value})


def format_time(epoch_s: float) -> str:
    dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(text: str) -> float:
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError("timestamp must be UTC with a Z suffix")
    return datetime.fromisoformat(text[:-1] + "+00:00").timestamp()
