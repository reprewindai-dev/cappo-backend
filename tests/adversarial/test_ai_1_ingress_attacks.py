"""AI-1 — Authority Ingress Completeness (out-of-process assault).

Hypothesis: an execution-plane workload receives no undeclared authority from
its launcher environment or working directory.

The repository does not currently expose a production subprocess launcher, so
this test is the declared CEM-1-R1 substrate harness. It deliberately constructs
a polluted parent environment and then launches the hostile workload through an
explicit deny-by-default child environment and empty working directory.

Falsifier: the hostile workload gains direct database/CAPPO authority, or the
child observes any of the deliberately forbidden parent authority variables.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SUBSTRATE_PYTHON_EXEC = sys.executable
HOSTILE_PAYLOAD = Path(__file__).with_name("ai1_hostile_workload.py").resolve()

# These are the only environment values this local substrate profile needs.
# The interpreter path and hostile payload path are absolute, so PATH is not
# required and is intentionally not inherited.
SAFE_CHILD_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
    "CAPPO_SUBSTRATE_PROFILE": "isolated-test",
}

FORBIDDEN_CANARIES = {
    "CAPPO_AUTHORIZATION_OVERRIDE": "ALLOW",
    "CAPPO_ADMIN_KEY": "ai1-canary-admin-key",
    "DATABASE_URL": "postgresql://ai1:forbidden@127.0.0.1:1/forbidden",
}


def _json_report(stdout: str) -> dict:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        pytest.fail("AI-1 hostile workload produced no JSON report")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        pytest.fail(f"AI-1 hostile workload produced invalid JSON: {lines[-1]!r}")
        raise AssertionError from exc


def test_ai_1_out_of_process_assault(tmp_path: Path) -> None:
    """Prove the hostile child receives no ambient parent authority."""
    polluted_parent = os.environ.copy()
    polluted_parent.update(FORBIDDEN_CANARIES)
    assert all(polluted_parent[key] == value for key, value in FORBIDDEN_CANARIES.items())

    # Deny-by-default is deliberate. Never derive the child environment from
    # ``os.environ`` and then try to subtract a list of known secret names.
    child_env = dict(SAFE_CHILD_ENV)
    assert not (set(child_env) & set(FORBIDDEN_CANARIES))

    result = subprocess.run(
        [SUBSTRATE_PYTHON_EXEC, str(HOSTILE_PAYLOAD)],
        env=child_env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    report = _json_report(result.stdout)
    hostile_report = report.get("report") if isinstance(report.get("report"), dict) else {}
    observed_env = set(hostile_report.get("suspicious_env_vars") or [])
    found_secret_files = hostile_report.get("found_secrets") or []

    # The child must not see any deliberately polluted authority variable, nor
    # inherit repository credential fixtures through its working directory.
    assert not (observed_env & set(FORBIDDEN_CANARIES)), report
    assert found_secret_files == [], report

    if result.returncode == 0:
        pytest.fail(
            "AI-1 Falsified: hostile workload successfully gained authority.\n"
            f"Output:\n{result.stdout}\n{result.stderr}"
        )

    assert result.returncode == 1, (
        f"Expected isolated hostile workload to fail with exit code 1, got {result.returncode}. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert report.get("status") == "SECURE", report
    assert report.get("reason") == "isolation_held", report
