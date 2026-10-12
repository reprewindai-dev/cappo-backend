"""Tests for the independent receipt verifier.

cbor2 is used HERE ONLY, to build receipts the same way CAPPO does. verify.py itself
must not import cbor2 (checked below).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import cbor2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

HERE = Path(__file__).resolve().parent
VERIFY = HERE / "verify.py"


def _load_verify():
    spec = importlib.util.spec_from_file_location("receipt_verify_under_test", VERIFY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


verify = _load_verify()


SAMPLE = {
    "execution_id": "exec_01HZX4",
    "mount_id": "mnt_7f3a",
    "token_id": "tok_91",
    "principal": "owner@example.test",
    "caller_spiffe_id": None,
    "executor_spiffe_id": None,
    "eei_id": None,
    "profile_id": None,
    "lease_id": None,
    "operator_id": None,
    "caller_cert_sha256": None,
    "capability_id": "document-reader@1",
    "biscuit_token_sha256": "ab" * 32,
    "action": "read",
    "resource": "doc/42",
    "policy_version": "1.0",
    "decision": "allow",
    "reason": "grant consumed — café \"quoted\" \\ tab\there \U0001F512",
    "timestamp": "2026-10-11T12:00:00.123456+00:00",
    "actioned_at": "2026-10-11T12:00:00.123456+00:00",
    "result_hash": None,
    "pgl_anchor_id": "anc_5",
    "receipt_schema_version": 2,
    "target_ref": "vault://doc/42",
    "envelope_digest": "cd" * 32,
}


def raw_public(key: ed25519.Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def kid_of(key: ed25519.Ed25519PrivateKey) -> str:
    return "ed25519-sha256:" + hashlib.sha256(raw_public(key)).hexdigest()


def jwk_of(key: ed25519.Ed25519PrivateKey) -> dict:
    return {
        "kid": kid_of(key),
        "kty": "OKP",
        "crv": "Ed25519",
        "x": base64.urlsafe_b64encode(raw_public(key)).rstrip(b"=").decode(),
        "alg": "EdDSA",
        "use": "sig",
    }


def keys_doc(*keys: ed25519.Ed25519PrivateKey) -> dict:
    return {"issuer": "cappo", "format": "JWK set", "keys": [jwk_of(k) for k in keys]}


def cappo_content_hash(fields: dict) -> str:
    text = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mint(fields: dict, key: ed25519.Ed25519PrivateKey, kid: str | None = None) -> bytes:
    """Build a COSE_Sign1 exactly as CAPPO's mint_signed_execution_evidence does."""
    protected = cbor2.dumps({1: -8}, canonical=True)
    payload = cbor2.dumps(fields, canonical=True)
    sig_data = cbor2.dumps(["Signature1", protected, b"", payload], canonical=True)
    signature = key.sign(sig_data)
    unprotected = {4: (kid or kid_of(key)).encode()}
    return cbor2.dumps(cbor2.CBORTag(18, [protected, unprotected, payload, signature]), canonical=True)


def bundle_for(cose: bytes, fields: dict, **overrides) -> dict:
    message = cbor2.loads(cose)
    bundle = {
        "receipt_id": "rcpt_anc_5",
        "mount_id": fields.get("mount_id"),
        "receipt_schema_version": fields.get("receipt_schema_version"),
        "content_hash": cappo_content_hash(fields),
        "signed": True,
        "format": "COSE_Sign1 (RFC 9052), alg EdDSA (-8), kid in unprotected header label 4",
        "kid": message.value[1][4].decode(),
        "cose_b64": base64.b64encode(cose).decode("ascii"),
        "key_endpoint": "/.well-known/evidence-keys",
    }
    bundle.update(overrides)
    return bundle


def run_cli(tmp_path: Path, bundle: dict, keys: dict, *extra: str) -> subprocess.CompletedProcess:
    bundle_path = tmp_path / "receipt.json"
    keys_path = tmp_path / "evidence-keys.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    keys_path.write_text(json.dumps(keys), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(VERIFY), "--bundle", str(bundle_path), "--keys", str(keys_path), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


@pytest.fixture()
def signer() -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.generate()


@pytest.fixture()
def other() -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.generate()


# ------------------------------------------------------------------ CLI scenarios


def test_valid_receipt_exits_zero(tmp_path, signer):
    cose = mint(SAMPLE, signer)
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE), keys_doc(signer))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "kid matches published key" in result.stdout and ": yes" in result.stdout
    assert "signature (Ed25519)" in result.stdout and ": VALID" in result.stdout
    assert "content_hash recomputed" in result.stdout and "matches" in result.stdout
    assert "mnt_7f3a" in result.stdout
    assert result.stdout.rstrip().endswith("RESULT: VALID")


def test_payload_tamper_exits_one(tmp_path, signer):
    cose = mint(SAMPLE, signer)
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE), keys_doc(signer), "--tamper", "payload")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert ": INVALID" in result.stdout
    assert "RESULT: INVALID" in result.stdout


def test_signature_tamper_exits_one(tmp_path, signer):
    cose = mint(SAMPLE, signer)
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE), keys_doc(signer), "--tamper", "signature")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "signature (Ed25519)" in result.stdout and ": INVALID" in result.stdout
    assert "content_hash recomputed" in result.stdout and "matches" in result.stdout
    assert "RESULT: INVALID" in result.stdout


def test_wrong_key_exits_one(tmp_path, signer, other):
    cose = mint(SAMPLE, signer)
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE), keys_doc(other))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "kid matches published key" in result.stdout
    assert "RESULT: INVALID" in result.stdout
    assert "no published key has kid" in result.stdout


def test_signed_by_other_key_under_published_kid_exits_one(tmp_path, signer, other):
    # An attacker signs with their own key but labels the receipt with the published kid.
    cose = mint(SAMPLE, other, kid=kid_of(signer))
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE), keys_doc(signer))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "signature (Ed25519)" in result.stdout and ": INVALID" in result.stdout


@pytest.mark.parametrize("publish_both", [False, True])
def test_kid_swapped_in_unprotected_header_exits_one(tmp_path, signer, other, publish_both):
    # Genuine signature by `signer`, but the unsigned kid now names `other`.
    cose = mint(SAMPLE, signer, kid=kid_of(other))
    keys = keys_doc(signer, other) if publish_both else keys_doc(signer)
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE), keys)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "RESULT: INVALID" in result.stdout
    if publish_both:
        assert "signature (Ed25519)" in result.stdout and ": INVALID" in result.stdout


def test_published_kid_not_matching_key_fingerprint_exits_one(tmp_path, signer):
    cose = mint(SAMPLE, signer, kid="ed25519-sha256:" + "0" * 64)
    doc = keys_doc(signer)
    doc["keys"][0]["kid"] = "ed25519-sha256:" + "0" * 64
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE), doc)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "kid is SHA-256 fingerprint of that key" in result.stdout
    assert "kid is not the SHA-256 fingerprint" in result.stdout


def test_content_hash_mismatch_exits_one(tmp_path, signer):
    cose = mint(SAMPLE, signer)
    bundle = bundle_for(cose, SAMPLE, content_hash="0" * 64)
    result = run_cli(tmp_path, bundle, keys_doc(signer))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "signature (Ed25519)" in result.stdout and ": VALID" in result.stdout
    assert "DIFFERS" in result.stdout
    assert "RESULT: INVALID" in result.stdout


def test_bundle_mount_id_mismatch_exits_one(tmp_path, signer):
    cose = mint(SAMPLE, signer)
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE, mount_id="mnt_other"), keys_doc(signer))
    assert result.returncode == 1
    assert "bundle mount_id differs" in result.stdout


def test_unsigned_bundle_exits_one(tmp_path, signer):
    cose = mint(SAMPLE, signer)
    result = run_cli(tmp_path, bundle_for(cose, SAMPLE, signed=False, cose_b64=None), keys_doc(signer))
    assert result.returncode == 1
    assert "not signed" in result.stdout


def test_v1_receipt_without_schema_version_is_valid(tmp_path, signer):
    fields = {k: v for k, v in SAMPLE.items() if k not in ("receipt_schema_version", "target_ref", "envelope_digest")}
    fields["resource"] = "*"
    cose = mint(fields, signer)
    bundle = bundle_for(cose, fields, receipt_schema_version=None)
    result = run_cli(tmp_path, bundle, keys_doc(signer))
    assert result.returncode == 0, result.stdout


def test_bad_json_is_usage_error(tmp_path, signer):
    (tmp_path / "receipt.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "keys.json").write_text(json.dumps(keys_doc(signer)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(VERIFY), "--bundle", str(tmp_path / "receipt.json"), "--keys", str(tmp_path / "keys.json")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2


def test_missing_arguments_is_usage_error():
    result = subprocess.run([sys.executable, str(VERIFY)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 2


# ------------------------------------------------------------------ unit checks


def test_verify_does_not_use_cbor2_or_cappo():
    source = VERIFY.read_text(encoding="utf-8")
    assert "import cbor2" not in source and "from cbor2" not in source
    assert "cappo_backend" not in source


def test_sig_structure_matches_cbor2(signer):
    for payload in (b"", b"x" * 23, b"y" * 24, b"z" * 300, b"w" * 70000, cbor2.dumps(SAMPLE, canonical=True)):
        protected = cbor2.dumps({1: -8}, canonical=True)
        expected = cbor2.dumps(["Signature1", protected, b"", payload], canonical=True)
        assert verify.sig_structure(protected, payload) == expected


def test_decoder_agrees_with_cbor2():
    samples = [
        SAMPLE,
        {"n": 0, "m": -1, "big": 2**64 - 1, "neg": -(2**64), "f": 1.5, "t": True, "x": None},
        {"nested": {"a": [1, 2, {"b": "c"}]}, "bytes": b"\x00\xff"},
        [23, 24, 255, 256, 65535, 65536, 2**32, -24, -25],
    ]
    for value in samples:
        assert verify.cbor_decode(cbor2.dumps(value, canonical=True)) == value


def test_content_hash_matches_cappo_rule():
    assert verify.content_hash_of(SAMPLE) == cappo_content_hash(SAMPLE)


@pytest.mark.parametrize(
    "data",
    [
        bytes([0xA2, 0x61, 0x61, 0x01, 0x61, 0x61, 0x02]),  # duplicate key "a"
        bytes([0x01, 0x02]),  # trailing byte
        bytes([0x9F, 0x01, 0xFF]),  # indefinite-length array
        bytes([0x62, 0xC3]),  # truncated text
        bytes([0x62, 0xC3, 0x28]),  # invalid UTF-8
        bytes([0x9B, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),  # absurd array length
    ],
)
def test_decoder_rejects_malformed(data):
    with pytest.raises(verify.CBORError):
        verify.cbor_decode(data)


def test_tamper_flips_exactly_one_bit(signer):
    cose = mint(SAMPLE, signer)
    for part in ("payload", "signature"):
        changed = verify.flip_bit_in(cose, part)
        diff = [i for i, (a, b) in enumerate(zip(cose, changed)) if a != b]
        assert len(diff) == 1 and bin(cose[diff[0]] ^ changed[diff[0]]).count("1") == 1
