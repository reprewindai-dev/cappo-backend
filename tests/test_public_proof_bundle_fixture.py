import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import cbor2


GENERATOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_public_proof_bundle.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_public_proof_bundle", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generate_bundle(tmp_path: Path) -> Path:
    out_dir = tmp_path / "proof-bundle"
    _load_generator().generate_bundle(str(out_dir))
    return out_dir


def _run_verifier(bundle_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "veklom-verify.py"],
        cwd=bundle_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_synthetic_fixture_baseline_is_structurally_valid(tmp_path: Path) -> None:
    bundle_dir = _generate_bundle(tmp_path)

    result = _run_verifier(bundle_dir)

    assert result.returncode == 0
    assert "Status: EXPERIMENTAL_STRUCTURE_VALIDATION" in result.stdout
    assert "FIXTURE_SIGNATURE_VALID=true" in result.stdout
    assert "FIXTURE_MERKLE_VALID=true" in result.stdout
    assert "EVIDENCE_CLASS=SYNTHETIC_FIXTURE" in result.stdout


def test_tampered_signature_is_rejected(tmp_path: Path) -> None:
    bundle_dir = _generate_bundle(tmp_path)
    receipt_path = bundle_dir / "receipt.cose"
    protected, unprotected, payload, signature = cbor2.loads(receipt_path.read_bytes())
    tampered_signature = bytes([signature[0] ^ 0x01]) + signature[1:]
    receipt_path.write_bytes(cbor2.dumps([protected, unprotected, payload, tampered_signature]))

    result = _run_verifier(bundle_dir)

    assert "FIXTURE_SIGNATURE_VALID=false" in result.stdout
    assert "FIXTURE_MERKLE_VALID=true" not in result.stdout


def test_tampered_payload_is_rejected_by_signature(tmp_path: Path) -> None:
    bundle_dir = _generate_bundle(tmp_path)
    receipt_path = bundle_dir / "receipt.cose"
    protected, unprotected, payload_cbor, signature = cbor2.loads(receipt_path.read_bytes())
    payload = cbor2.loads(payload_cbor)
    payload["resource"] = "/fixture/resource/tampered"
    receipt_path.write_bytes(
        cbor2.dumps([protected, unprotected, cbor2.dumps(payload), signature])
    )

    result = _run_verifier(bundle_dir)

    assert "FIXTURE_SIGNATURE_VALID=false" in result.stdout
    assert "FIXTURE_MERKLE_VALID=true" not in result.stdout


def test_tampered_merkle_proof_is_rejected(tmp_path: Path) -> None:
    bundle_dir = _generate_bundle(tmp_path)
    proof_path = bundle_dir / "proof.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["siblings"][0] = "00" * 32
    proof_path.write_text(json.dumps(proof, indent=2), encoding="utf-8")

    result = _run_verifier(bundle_dir)

    assert "FIXTURE_SIGNATURE_VALID=true" in result.stdout
    assert "FIXTURE_MERKLE_VALID=false (root mismatch)" in result.stdout
    assert "FIXTURE_MERKLE_VALID=true" not in result.stdout
