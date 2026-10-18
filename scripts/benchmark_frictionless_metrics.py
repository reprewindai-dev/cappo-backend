#!/usr/bin/env python3
"""Fail-closed friction benchmark for the Wedge Elimination Dossier.

No missing measurement may be reported as PASS.

Inputs:
  CAPPO_BENCH_BASE_URL (default http://127.0.0.1:8002)
  CAPPO_BENCH_BEARER_TOKEN (optional if test runtime does not require it)
  CAPPO_BENCH_CAPABILITY_ID (required for live TTFGC)
  CAPPO_BENCH_ACTION_PAYLOAD (JSON, default {})
  CAPPO_BENCH_OUTCOME_UNKNOWN_EXECUTION_ID (required for recovery metric)
  CAPPO_BENCH_TRIAL_MANIFEST (JSON file with observed developer-friction data)
  CAPPO_BENCH_WORKLOAD_CAPTURE (file containing workload env/memory/debug capture)
  CAPPO_BENCH_SECRET_MARKERS (comma-separated known target-secret markers)

Trial manifest schema:
{
  "config_files_touched": ["..."],
  "manual_approval_steps": 0,
  "additional_components_installed": ["..."],
  "api_setup_calls_before_dispatch": 1
}

Exit codes:
  0 = all seven frozen metrics PASS
  1 = at least one metric FAIL
  2 = no FAIL, but at least one metric INDETERMINATE
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
OUTFILE = EVIDENCE_DIR / "frictionless_benchmark_baseline.json"

THRESHOLDS = {
    "FRIC-01": "< 5.0 seconds",
    "FRIC-02": "<= 2 files",
    "FRIC-03": "0 manual steps",
    "FRIC-04": "<= 1 additional component",
    "FRIC-05": "<= 1 setup call before dispatch",
    "FRIC-06": "<= 1 automated reconciliation call",
    "FRIC-07": "0 known target-secret markers observed in workload capture",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git_sha() -> Optional[str]:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return None


def metric(metric_id: str, status: str, measured: Any = None, evidence: Any = None, error: Optional[str] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "metric_id": metric_id,
        "threshold": THRESHOLDS[metric_id],
        "status": status,
        "measured_value": measured,
    }
    if evidence is not None:
        item["evidence"] = evidence
    if error is not None:
        item["error"] = error
    return item


def load_trial_manifest() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    manifest_path = os.environ.get("CAPPO_BENCH_TRIAL_MANIFEST")
    if not manifest_path:
        return None, "CAPPO_BENCH_TRIAL_MANIFEST not supplied"
    path = Path(manifest_path)
    if not path.exists():
        return None, f"trial manifest not found: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"trial manifest invalid JSON: {exc}"


def live_ttfgc() -> Tuple[Optional[float], Optional[Dict[str, Any]], Optional[str]]:
    capability_id = os.environ.get("CAPPO_BENCH_CAPABILITY_ID")
    if not capability_id:
        return None, None, "CAPPO_BENCH_CAPABILITY_ID not supplied"
    try:
        from sdk.python.cappo_client import CappoClient
    except Exception as exc:
        return None, None, f"SDK import failed: {exc}"

    base_url = os.environ.get("CAPPO_BENCH_BASE_URL", "http://127.0.0.1:8002")
    bearer = os.environ.get("CAPPO_BENCH_BEARER_TOKEN")
    try:
        payload = json.loads(os.environ.get("CAPPO_BENCH_ACTION_PAYLOAD", "{}"))
    except Exception as exc:
        return None, None, f"CAPPO_BENCH_ACTION_PAYLOAD invalid JSON: {exc}"

    client = CappoClient(base_url=base_url, bearer_token=bearer, timeout_s=10.0)
    envelope = {
        "memory_max_bytes": 67108864,
        "compute_fuel_units": 50000000,
        "wall_deadline_ms": 300,
        "allow_network": False,
    }

    started = time.perf_counter()
    try:
        mount = client.mount_authority(capability_id, envelope)
        dispatch = client.dispatch_consequence(
            mount.lease_id,
            mount.execution_id,
            mount.envelope_digest,
            payload,
        )
    except Exception as exc:
        return None, None, f"live governed consequence failed: {exc}"
    elapsed = time.perf_counter() - started
    return elapsed, {
        "base_url": base_url,
        "mount_id": mount.mount_id,
        "lease_id": mount.lease_id,
        "execution_id": mount.execution_id,
        "dispatch_id": dispatch.dispatch_id,
        "dispatch_status": dispatch.status,
    }, None


def live_recovery() -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    execution_id = os.environ.get("CAPPO_BENCH_OUTCOME_UNKNOWN_EXECUTION_ID")
    if not execution_id:
        return None, None, "CAPPO_BENCH_OUTCOME_UNKNOWN_EXECUTION_ID not supplied"
    try:
        from sdk.python.cappo_client import CappoClient
        client = CappoClient(
            base_url=os.environ.get("CAPPO_BENCH_BASE_URL", "http://127.0.0.1:8002"),
            bearer_token=os.environ.get("CAPPO_BENCH_BEARER_TOKEN"),
            timeout_s=10.0,
        )
        response = client.reconcile_consequence(execution_id)
        return 1, {
            "execution_id": execution_id,
            "status": response.status,
            "finality_state": response.finality_state,
        }, None
    except Exception as exc:
        return None, None, f"live reconciliation failed: {exc}"


def secret_isolation() -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    capture_path = os.environ.get("CAPPO_BENCH_WORKLOAD_CAPTURE")
    markers_raw = os.environ.get("CAPPO_BENCH_SECRET_MARKERS")
    if not capture_path or not markers_raw:
        return None, None, "CAPPO_BENCH_WORKLOAD_CAPTURE and CAPPO_BENCH_SECRET_MARKERS are required"
    path = Path(capture_path)
    if not path.exists():
        return None, None, f"workload capture not found: {path}"
    markers = [x.strip() for x in markers_raw.split(",") if x.strip()]
    if not markers:
        return None, None, "no secret markers supplied"
    raw = path.read_bytes()
    hits: List[str] = [m for m in markers if m.encode("utf-8") in raw]
    return len(hits), {"capture": str(path), "marker_count": len(markers), "hits": hits}, None


def main() -> int:
    metrics: List[Dict[str, Any]] = []
    manifest, manifest_error = load_trial_manifest()

    elapsed, flow_evidence, flow_error = live_ttfgc()
    if elapsed is None:
        metrics.append(metric("FRIC-01", "INDETERMINATE", error=flow_error))
    else:
        metrics.append(metric("FRIC-01", "PASS" if elapsed < 5.0 else "FAIL", round(elapsed, 4), flow_evidence))

    if manifest is None:
        for metric_id in ("FRIC-02", "FRIC-03", "FRIC-04", "FRIC-05"):
            metrics.append(metric(metric_id, "INDETERMINATE", error=manifest_error))
    else:
        files = manifest.get("config_files_touched")
        if isinstance(files, list):
            metrics.append(metric("FRIC-02", "PASS" if len(files) <= 2 else "FAIL", len(files), files))
        else:
            metrics.append(metric("FRIC-02", "INDETERMINATE", error="config_files_touched missing/not a list"))

        approvals = manifest.get("manual_approval_steps")
        if isinstance(approvals, int):
            metrics.append(metric("FRIC-03", "PASS" if approvals == 0 else "FAIL", approvals))
        else:
            metrics.append(metric("FRIC-03", "INDETERMINATE", error="manual_approval_steps missing/not an integer"))

        components = manifest.get("additional_components_installed")
        if isinstance(components, list):
            metrics.append(metric("FRIC-04", "PASS" if len(components) <= 1 else "FAIL", len(components), components))
        else:
            metrics.append(metric("FRIC-04", "INDETERMINATE", error="additional_components_installed missing/not a list"))

        setup_calls = manifest.get("api_setup_calls_before_dispatch")
        if isinstance(setup_calls, int):
            metrics.append(metric("FRIC-05", "PASS" if setup_calls <= 1 else "FAIL", setup_calls))
        else:
            metrics.append(metric("FRIC-05", "INDETERMINATE", error="api_setup_calls_before_dispatch missing/not an integer"))

    recovery_calls, recovery_evidence, recovery_error = live_recovery()
    if recovery_calls is None:
        metrics.append(metric("FRIC-06", "INDETERMINATE", error=recovery_error))
    else:
        metrics.append(metric("FRIC-06", "PASS" if recovery_calls <= 1 else "FAIL", recovery_calls, recovery_evidence))

    exposed, secret_evidence, secret_error = secret_isolation()
    if exposed is None:
        metrics.append(metric("FRIC-07", "INDETERMINATE", error=secret_error))
    else:
        metrics.append(metric("FRIC-07", "PASS" if exposed == 0 else "FAIL", exposed, secret_evidence))

    statuses = [m["status"] for m in metrics]
    if "FAIL" in statuses:
        overall = "FAIL"
        rc = 1
    elif "INDETERMINATE" in statuses:
        overall = "INDETERMINATE"
        rc = 2
    else:
        overall = "PASS"
        rc = 0

    summary = {
        "generated_at": utc_now(),
        "git_sha": git_sha(),
        "branch": os.environ.get("GITHUB_REF_NAME"),
        "overall_frictionless_status": overall,
        "metrics_evaluated": len(metrics),
        "metrics_passed": sum(1 for m in metrics if m["status"] == "PASS"),
        "metrics_failed": sum(1 for m in metrics if m["status"] == "FAIL"),
        "metrics_indeterminate": sum(1 for m in metrics if m["status"] == "INDETERMINATE"),
        "metrics": metrics,
    }
    OUTFILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
