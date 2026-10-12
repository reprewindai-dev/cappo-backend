#!/usr/bin/env python3
"""Independent verifier for Veklom CAPPO execution receipts.

This file shares no code with CAPPO. It needs only the Python standard library and
the `cryptography` package (for Ed25519). The CBOR decoder and the COSE Sig_structure
encoder below are written from RFC 8949 and RFC 9052, not taken from CAPPO.

A receipt bundle (GET /v1/capability/receipts/{id}) carries a COSE_Sign1 message:

    18([ protected: bstr .cbor {1: -8},   ; alg EdDSA
         unprotected: {4: kid},           ; kid = "ed25519-sha256:" + sha256(pubkey).hex()
         payload: bstr .cbor {fields},    ; the signed receipt fields
         signature: bstr ])               ; Ed25519 over Sig_structure

    Sig_structure = ["Signature1", protected, h'', payload]

The kid sits in the unprotected header, so the signature does not cover it. This tool
therefore checks separately that the kid is the SHA-256 fingerprint of the key that
verified the signature and equals the kid the key publisher assigned to that key.

Usage:
    python verify.py --bundle receipt.json --keys evidence-keys.json
    python verify.py --bundle receipt.json --keys-url http://127.0.0.1:28202/.well-known/evidence-keys
    python verify.py --bundle receipt.json --keys evidence-keys.json --tamper signature

Exit codes: 0 receipt valid, 1 receipt invalid, 2 usage or input-file error.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import struct
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

KID_PREFIX = "ed25519-sha256:"
COSE_SIGN1_TAG = 18
COSE_HEADER_ALG = 1
COSE_HEADER_CRIT = 2
COSE_HEADER_KID = 4
COSE_ALG_EDDSA = -8
MAX_NESTING = 64
MAX_KEYS_DOCUMENT_BYTES = 1 << 20

EXIT_VALID = 0
EXIT_INVALID = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------- CBOR


class CBORError(ValueError):
    """The bytes are not well-formed CBOR of the kinds a receipt uses."""


class Tag:
    """A CBOR tag the decoder does not interpret."""

    def __init__(self, number: int, value: Any) -> None:
        self.number = number
        self.value = value

    def __repr__(self) -> str:
        return f"Tag({self.number}, {self.value!r})"


class _Decoder:
    """Strict decoder: definite lengths only, no duplicate map keys, no trailing bytes."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise CBORError(f"truncated input at byte {self.pos} (needed {n} more bytes)")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def _argument(self, info: int) -> int:
        if info < 24:
            return info
        if info == 24:
            return self._take(1)[0]
        if info == 25:
            return struct.unpack(">H", self._take(2))[0]
        if info == 26:
            return struct.unpack(">I", self._take(4))[0]
        if info == 27:
            return struct.unpack(">Q", self._take(8))[0]
        if info == 31:
            raise CBORError(f"indefinite-length item at byte {self.pos - 1} is not allowed")
        raise CBORError(f"reserved additional information {info} at byte {self.pos - 1}")

    def item(self, depth: int = 0) -> Any:
        if depth > MAX_NESTING:
            raise CBORError("nesting too deep")
        start = self.pos
        initial = self._take(1)[0]
        major, info = initial >> 5, initial & 0x1F

        if major == 7:
            if info == 20:
                return False
            if info == 21:
                return True
            if info == 22:
                return None
            if info == 25:
                return struct.unpack(">e", self._take(2))[0]
            if info == 26:
                return struct.unpack(">f", self._take(4))[0]
            if info == 27:
                return struct.unpack(">d", self._take(8))[0]
            if info == 31:
                raise CBORError(f"unexpected break code at byte {start}")
            raise CBORError(f"unsupported simple value (info {info}) at byte {start}")

        n = self._argument(info)
        if major == 0:
            return n
        if major == 1:
            return -1 - n
        if major == 2:
            return bytes(self._take(n))
        if major == 3:
            raw = self._take(n)
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CBORError(f"text string at byte {start} is not valid UTF-8") from exc
        if major == 4:
            if n > len(self.data) - self.pos:
                raise CBORError(f"array length {n} at byte {start} exceeds the input")
            return [self.item(depth + 1) for _ in range(n)]
        if major == 5:
            if 2 * n > len(self.data) - self.pos:
                raise CBORError(f"map length {n} at byte {start} exceeds the input")
            out: Dict[Any, Any] = {}
            for _ in range(n):
                key_at = self.pos
                key = self.item(depth + 1)
                if not isinstance(key, (str, int, bytes)) or isinstance(key, bool):
                    raise CBORError(f"unsupported map key type at byte {key_at}")
                # Python treats 1 and 1.0 / True as equal; restricting key types above avoids that.
                if key in out:
                    raise CBORError(f"duplicate map key {key!r} at byte {key_at}")
                out[key] = self.item(depth + 1)
            return out
        # major == 6
        return Tag(n, self.item(depth + 1))


def cbor_decode(data: bytes) -> Any:
    """Decode exactly one CBOR item that spans all of ``data``."""
    decoder = _Decoder(data)
    value = decoder.item()
    if decoder.pos != len(data):
        raise CBORError(f"{len(data) - decoder.pos} trailing bytes after the CBOR item")
    return value


def _cbor_head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 0x100:
        return bytes([(major << 5) | 24, n])
    if n < 0x10000:
        return bytes([(major << 5) | 25]) + struct.pack(">H", n)
    if n < 0x100000000:
        return bytes([(major << 5) | 26]) + struct.pack(">I", n)
    return bytes([(major << 5) | 27]) + struct.pack(">Q", n)


def sig_structure(protected: bytes, payload: bytes) -> bytes:
    """RFC 9052 section 4.4 Sig_structure for COSE_Sign1 with empty external_aad.

    The protected and payload byte strings are used exactly as they appear in the
    message; only the array wrapper, the context string and the empty bstr are encoded.
    """
    context = b"Signature1"
    return b"".join(
        [
            _cbor_head(4, 4),
            _cbor_head(3, len(context)),
            context,
            _cbor_head(2, len(protected)),
            protected,
            _cbor_head(2, 0),
            _cbor_head(2, len(payload)),
            payload,
        ]
    )


class CoseSign1:
    def __init__(self, data: bytes) -> None:
        dec = _Decoder(data)
        initial = dec._take(1)[0]
        if initial >> 5 != 6:
            raise CBORError("message does not start with a CBOR tag (expected tag 18, COSE_Sign1)")
        tag = dec._argument(initial & 0x1F)
        if tag != COSE_SIGN1_TAG:
            raise CBORError(f"message carries CBOR tag {tag}, expected 18 (COSE_Sign1)")
        initial = dec._take(1)[0]
        if initial >> 5 != 4 or dec._argument(initial & 0x1F) != 4:
            raise CBORError("COSE_Sign1 must be an array of exactly 4 elements")
        parts: List[Any] = []
        ends: List[int] = []
        for _ in range(4):
            parts.append(dec.item(1))
            ends.append(dec.pos)
        if dec.pos != len(data):
            raise CBORError(f"{len(data) - dec.pos} trailing bytes after the COSE_Sign1 message")
        protected, unprotected, payload, signature = parts
        if not isinstance(protected, bytes):
            raise CBORError("protected header is not a byte string")
        if not isinstance(unprotected, dict):
            raise CBORError("unprotected header is not a map")
        if not isinstance(payload, bytes):
            raise CBORError("payload is not a byte string (detached payloads are not supported)")
        if not isinstance(signature, bytes):
            raise CBORError("signature is not a byte string")
        self.protected = protected
        self.unprotected = unprotected
        self.payload = payload
        self.signature = signature
        # Byte offsets where the payload and signature contents end inside the message;
        # a bstr's content is the last len(value) bytes of its encoded span.
        self.payload_end = ends[2]
        self.signature_end = ends[3]


def flip_bit_in(data: bytes, part: str) -> bytes:
    """Flip the low bit of the middle byte of the payload or signature inside ``data``."""
    msg = CoseSign1(data)
    if part == "payload":
        end, length = msg.payload_end, len(msg.payload)
    else:
        end, length = msg.signature_end, len(msg.signature)
    if length == 0:
        raise CBORError(f"{part} is empty; nothing to tamper with")
    at = end - length + length // 2
    out = bytearray(data)
    out[at] ^= 0x01
    return bytes(out)


# --------------------------------------------------------------------------- checks


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or not text or any(c in text for c in "+/= \n\r\t"):
        raise ValueError("not unpadded base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def kid_for_raw_key(raw: bytes) -> str:
    return KID_PREFIX + hashlib.sha256(raw).hexdigest()


def _json_default(value: Any) -> Any:
    # CAPPO hashes with json.dumps(default=str); bytes are the only non-JSON type a
    # CBOR payload can hand back that str() renders the same way on both sides.
    if isinstance(value, bytes):
        return str(value)
    raise TypeError(f"payload contains a {type(value).__name__}, which has no agreed JSON form")


def content_hash_of(payload: Any) -> str:
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Report:
    def __init__(self) -> None:
        self.lines: List[Tuple[str, str]] = []
        self.failures: List[str] = []
        self.fields: Optional[Dict[Any, Any]] = None

    def line(self, label: str, value: str) -> None:
        self.lines.append((label, value))

    def fail(self, reason: str) -> None:
        self.failures.append(reason)

    @property
    def valid(self) -> bool:
        return not self.failures


def _jwk_problem(jwk: Any) -> Optional[str]:
    if not isinstance(jwk, dict):
        return "key entry is not a JSON object"
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        return f"key is not an Ed25519 OKP key (kty={jwk.get('kty')!r}, crv={jwk.get('crv')!r})"
    if "alg" in jwk and jwk["alg"] not in ("EdDSA", "Ed25519"):
        return f"key alg is {jwk['alg']!r}, expected EdDSA"
    if "use" in jwk and jwk["use"] != "sig":
        return f"key use is {jwk['use']!r}, expected sig"
    try:
        raw = b64url_decode(jwk.get("x"))
    except (ValueError, binascii.Error):
        return "key x is not unpadded base64url"
    if len(raw) != 32:
        return f"key x decodes to {len(raw)} bytes, expected 32"
    return None


def verify_bundle(bundle: Any, keys_doc: Any, tamper: Optional[str] = None) -> Report:
    """Run every check and collect human-readable lines plus failure reasons."""
    r = Report()

    if not isinstance(bundle, dict):
        r.fail("bundle is not a JSON object")
        return r
    keys = keys_doc.get("keys") if isinstance(keys_doc, dict) else None
    if not isinstance(keys, list):
        r.fail('published key document has no "keys" list')
        return r

    r.line("receipt_id (bundle, not signed)", str(bundle.get("receipt_id")))
    cose_b64 = bundle.get("cose_b64")
    if bundle.get("signed") is not True or not isinstance(cose_b64, str) or not cose_b64:
        r.line("signed", "no")
        r.fail("receipt is not signed (bundle has no COSE bytes)")
        return r
    try:
        cose = base64.b64decode(cose_b64, validate=True)
    except (ValueError, binascii.Error):
        r.fail("cose_b64 is not valid standard base64")
        return r

    if tamper:
        try:
            cose = flip_bit_in(cose, tamper)
        except CBORError as exc:
            r.fail(f"cannot tamper: {exc}")
            return r
        r.line("tamper", f"flipped one bit in the middle of the {tamper} bytes")

    try:
        msg = CoseSign1(cose)
    except CBORError as exc:
        r.line("COSE_Sign1 structure", f"malformed ({exc})")
        r.fail(f"receipt is not a well-formed COSE_Sign1: {exc}")
        return r
    r.line("COSE_Sign1 structure", "well-formed (tag 18, 4 elements)")

    # Protected header: the algorithm is covered by the signature.
    try:
        protected = cbor_decode(msg.protected)
    except CBORError as exc:
        protected = None
        r.fail(f"protected header does not parse: {exc}")
    if isinstance(protected, dict):
        alg = protected.get(COSE_HEADER_ALG)
        if alg == COSE_ALG_EDDSA:
            r.line("algorithm (protected header)", "EdDSA (-8)")
        else:
            r.line("algorithm (protected header)", f"{alg!r} (not EdDSA)")
            r.fail(f"protected header alg is {alg!r}, expected -8 (EdDSA)")
        if COSE_HEADER_CRIT in protected:
            r.fail("protected header lists critical parameters this verifier does not understand")
    elif protected is not None:
        r.fail("protected header is not a CBOR map")

    # kid: unprotected, so it is only a hint until it is tied to the verifying key.
    kid_raw = msg.unprotected.get(COSE_HEADER_KID)
    kid: Optional[str] = None
    if isinstance(kid_raw, bytes):
        try:
            kid = kid_raw.decode("utf-8")
        except UnicodeDecodeError:
            kid = None
    elif isinstance(kid_raw, str):
        kid = kid_raw
    r.line("kid (unprotected header, not signed)", kid if kid is not None else "missing")
    if kid is None:
        r.fail("receipt names no key id (unprotected header label 4)")

    bundle_kid = bundle.get("kid")
    if bundle_kid is not None and kid is not None:
        same = bundle_kid == kid
        r.line("bundle kid equals COSE kid", "yes" if same else f"no (bundle says {bundle_kid})")
        if not same:
            r.fail("bundle kid differs from the kid inside the COSE message")

    public_key: Optional[Ed25519PublicKey] = None
    if kid is not None:
        matches = [k for k in keys if isinstance(k, dict) and k.get("kid") == kid]
        r.line("kid matches published key", "yes" if matches else "no")
        if not matches:
            published = ", ".join(str(k.get("kid")) for k in keys if isinstance(k, dict)) or "none"
            r.fail(f"no published key has kid {kid} (published: {published})")
        elif len(matches) > 1:
            r.fail("the key document publishes the same kid more than once")
        else:
            jwk = matches[0]
            problem = _jwk_problem(jwk)
            if problem:
                r.fail(f"published key is unusable: {problem}")
            else:
                raw = b64url_decode(jwk["x"])
                fingerprint = kid_for_raw_key(raw)
                ok = fingerprint == kid == jwk.get("kid")
                r.line("kid is SHA-256 fingerprint of that key", "yes" if ok else f"no (key fingerprint {fingerprint})")
                if not ok:
                    r.fail("kid is not the SHA-256 fingerprint of the published key")
                public_key = Ed25519PublicKey.from_public_bytes(raw)

    if public_key is None:
        r.line("signature (Ed25519)", "NOT CHECKED (no usable key for this kid)")
    elif len(msg.signature) != 64:
        r.line("signature (Ed25519)", "INVALID")
        r.fail(f"signature is {len(msg.signature)} bytes, Ed25519 signatures are 64")
    else:
        try:
            public_key.verify(msg.signature, sig_structure(msg.protected, msg.payload))
            r.line("signature (Ed25519)", "VALID")
        except InvalidSignature:
            r.line("signature (Ed25519)", "INVALID")
            r.fail("signature does not verify: the signed bytes were changed or another key signed them")

    try:
        fields = cbor_decode(msg.payload)
    except CBORError as exc:
        r.line("payload", f"no longer parses ({exc})")
        r.fail("payload no longer parses as CBOR")
        return r
    if not isinstance(fields, dict):
        r.line("payload", "not a CBOR map")
        r.fail("payload is not a CBOR map")
        return r
    r.fields = fields

    expected_hash = bundle.get("content_hash")
    try:
        recomputed = content_hash_of(fields)
    except (TypeError, ValueError) as exc:
        recomputed = None
        r.line("content_hash recomputed", f"CANNOT RECOMPUTE ({exc})")
        r.fail(f"content_hash cannot be recomputed: {exc}")
    if recomputed is not None:
        if not isinstance(expected_hash, str):
            r.line("content_hash recomputed", f"{recomputed} (bundle has none to compare)")
            r.fail("bundle carries no content_hash")
        elif expected_hash.strip().lower() == recomputed:
            r.line("content_hash recomputed", f"matches ({recomputed})")
        else:
            r.line("content_hash recomputed", f"DIFFERS (signed fields hash to {recomputed}, bundle says {expected_hash})")
            r.fail("content_hash in the bundle is not the hash of the signed fields")

    # Bundle metadata shown next to the receipt must agree with what was signed.
    if "mount_id" in bundle and "mount_id" in fields:
        same = bundle["mount_id"] == fields["mount_id"]
        r.line("bundle mount_id equals signed mount_id", "yes" if same else "no")
        if not same:
            r.fail("bundle mount_id differs from the signed mount_id")
    if "receipt_schema_version" in bundle:
        shown = bundle["receipt_schema_version"] or 1
        signed_version = fields.get("receipt_schema_version", 1)
        same = shown == signed_version
        r.line("bundle schema version equals signed version", "yes" if same else f"no ({shown} vs {signed_version})")
        if not same:
            r.fail("bundle receipt_schema_version differs from the signed version")
    return r


# --------------------------------------------------------------------------- CLI


def _format_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return "h'" + value.hex() + "'"
    if isinstance(value, (dict, list, Tag)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)
        except (TypeError, ValueError):
            return repr(value)
    return str(value)


def render(report: Report) -> str:
    out: List[str] = []
    width = max((len(label) for label, _ in report.lines), default=0)
    for label, value in report.lines:
        out.append(f"{label.ljust(width)} : {value}")
    if report.fields is not None:
        out.append("")
        out.append("Signed fields (decoded from the COSE payload):")
        keys = sorted(report.fields, key=lambda k: (not isinstance(k, str), str(k)))
        kwidth = max((len(str(k)) for k in keys), default=0)
        for k in keys:
            out.append(f"  {str(k).ljust(kwidth)}  {_format_value(report.fields[k])}")
    out.append("")
    if report.valid:
        out.append("RESULT: VALID")
    else:
        out.append(f"RESULT: INVALID — {report.failures[0]}")
        for extra in report.failures[1:]:
            out.append(f"        also: {extra}")
    return "\n".join(out)


class UsageError(Exception):
    pass


def _load_json_file(path: str, what: str) -> Any:
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8-sig"))
    except OSError as exc:
        raise UsageError(f"cannot read {what} file {path}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError(f"{what} file {path} is not valid JSON: {exc}") from exc


def _fetch_keys(url: str) -> Any:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise UsageError("--keys-url must be an http:// or https:// URL")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(MAX_KEYS_DOCUMENT_BYTES + 1)
    except OSError as exc:
        raise UsageError(f"cannot fetch published keys from {url}: {exc}") from exc
    if len(body) > MAX_KEYS_DOCUMENT_BYTES:
        raise UsageError("published key document is larger than 1 MiB")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError(f"published key document at {url} is not valid JSON: {exc}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(
        description="Verify a Veklom CAPPO execution receipt without trusting CAPPO.",
    )
    parser.add_argument("--bundle", required=True, help="receipt bundle JSON (GET /v1/capability/receipts/{id})")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--keys", help="published key JSON file (/.well-known/evidence-keys)")
    source.add_argument("--keys-url", help="URL of the published key document")
    parser.add_argument(
        "--tamper",
        choices=("payload", "signature"),
        help="flip one bit in the middle of the payload or signature first, to show a failure",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_VALID

    try:
        bundle = _load_json_file(args.bundle, "bundle")
        keys_doc = _load_json_file(args.keys, "keys") if args.keys else _fetch_keys(args.keys_url)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(keys_doc, dict) or not isinstance(keys_doc.get("keys"), list):
        print('error: published key document has no "keys" list', file=sys.stderr)
        return EXIT_USAGE

    report = verify_bundle(bundle, keys_doc, tamper=args.tamper)
    print(render(report))
    return EXIT_VALID if report.valid else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
