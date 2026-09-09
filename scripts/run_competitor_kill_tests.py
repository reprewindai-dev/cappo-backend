#!/usr/bin/env python3
"""Evidence-gated competitor reproduction verifier for the Wedge Elimination Dossier.

This script does NOT simulate AWS, Google, Microsoft, Permit/Cerbos, SPIFFE,
CloudWatch, Cloud Logging, or any other external system. It only validates
actual reproduction evidence that has already been captured by an operator or
provider-specific adapter.

Input manifest (default):
  docs/evidence/competitor_reproduction_manifest.json

Each test entry must point to real raw evidence files. Missing evidence remains
INDETERMINATE and can never become PASS by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "evidence" / "competitor_reproduction_manifest.json"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "evidence" / "competitor_kill_test_results.json"
REQUIRED_TESTS = ["KILL-01", "KILL-02", "KILL-03", "KILL-04", "KILL-05"]
ALLOWED_RESULTS = {"VALID", "INVALID", "INDETERMINATE"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_evidence_paths(raw_paths: List[str]) -> List[Dict[str, str]]:
    resolved = []
    for raw in raw_paths:
        p = Path(raw)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Evidence file not found: {raw}")
        resolved.append({
            "path": str(p.relative_to(REPO_ROOT)) if REPO_ROOT in p.parents else str(p),
            "sha256": sha256_file(p),
        })
    return resolved


def validate_test(test_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    provider = entry.get("provider")
    stack = entry.get("stack")
    result = entry.get("result", "INDETERMINATE")
    evidence_paths = entry.get("evidence_files") or []
    reproduction_command = entry.get("reproduction_command")
    provider_version = entry.get("provider_version")
    executed_at = entry.get("executed_at")

    reasons = []
    if result not in ALLOWED_RESULTS:
        reasons.append(f"invalid result value: {result}")
        result = "INDETERMINATE"
    if not provider:
        reasons.append("missing provider")
    if not stack:
        reasons.append("missing stack description")
    if not provider_version:
        reasons.append("missing provider/service version or dated API surface")
    if not reproduction_command:
        reasons.append("missing reproducible command/procedure")
    if not executed_at:
        reasons.append("missing measured execution timestamp")
    if not evidence_paths:
        reasons.append("no raw evidence files supplied")

    evidence = []
    if evidence_paths:
        try:
            evidence = resolve_evidence_paths(evidence_paths)
        except FileNotFoundError as exc:
            reasons.append(str(exc))

    # A conclusive result is forbidden unless the evidence gate is complete.
    if reasons:
        result = "INDETERMINATE"

    return {
        "test_id": test_id,
        "provider": provider,
        "stack": stack,
        "provider_version": provider_version,
        "executed_at": executed_at,
        "reproduction_command": reproduction_command,
        "result": result,
        "evidence": evidence,
        "validation_reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"INDETERMINATE: manifest missing: {manifest_path}", file=sys.stderr)
        return 2

    manifest = load_json(manifest_path)
    tests = manifest.get("tests") or {}

    results = []
    for test_id in REQUIRED_TESTS:
        entry = tests.get(test_id)
        if not isinstance(entry, dict):
            results.append({
                "test_id": test_id,
                "result": "INDETERMINATE",
                "validation_reasons": ["test entry missing from manifest"],
                "evidence": [],
            })
            continue
        results.append(validate_test(test_id, entry))

    conclusive = all(r["result"] in {"VALID", "INVALID"} for r in results)
    all_competitor_reproductions_failed_w1 = conclusive and all(r["result"] == "INVALID" for r in results)

    if all_competitor_reproductions_failed_w1:
        verdict = "COMPETITOR_REPRODUCTION_GAPS_OBSERVED"
    elif conclusive:
        verdict = "COMPETITOR_REPRODUCTION_RESULTS_MIXED_OR_REPRODUCED"
    else:
        verdict = "INDETERMINATE"

    artifact = {
        "generated_at": now_iso(),
        "source_manifest": str(manifest_path),
        "suite": "Evidence-gated competitor reproduction verifier",
        "results": results,
        "verdict": verdict,
        "claim_rule": (
            "This verifier never emits UNIQUE_WEDGE_SUPPORTED. That dossier verdict requires "
            "canonical Veklom W1-A..W1-D integration, measured friction evidence, and fair "
            "provider-specific reproductions beyond this file-presence/integrity gate."
        ),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "verdict": verdict}, indent=2))

    # Non-zero while evidence is incomplete, suitable for a proof gate.
    return 0 if conclusive else 2


if __name__ == "__main__":
    raise SystemExit(main())
