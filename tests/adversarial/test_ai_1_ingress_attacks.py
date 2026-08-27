"""
AI-1 authority-ingress adversarial harness hardening.

This test validates a narrow property: the local subprocess harness does not pass
parent-process secrets into the hostile child and the resulting evidence does not
reprint a sentinel secret. It is not proof of Lockerphycer/container isolation,
network-namespace enforcement, filesystem confinement, or CEM-1 conformance.
"""

import json
import os
import subprocess
import sys

import pytest


SUBSTRATE_PYTHON_EXEC = sys.executable
HOSTILE_PAYLOAD = os.path.join(os.path.dirname(__file__), "ai1_hostile_workload.py")
SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "TMP", "TEMP")


def _hostile_environment() -> dict[str, str]:
    """Build the explicit allowlist passed across the local harness boundary."""
    env = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    # Preserve local package discovery without copying arbitrary parent variables.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env["PYTHONPATH"] = repo_root
    return env


def test_ai_1_out_of_process_assault(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local hostile child receives no parent secret and emits secret-safe evidence."""
    sentinel = "cem1-parent-secret-must-never-cross-boundary"
    monkeypatch.setenv("CEM1_TEST_SECRET", sentinel)

    result = subprocess.run(
        [SUBSTRATE_PYTHON_EXEC, HOSTILE_PAYLOAD],
        env=_hostile_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    # Evidence must remain safe even when the hostile payload or harness fails.
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr

    if result.returncode == 0:
        pytest.fail("AI-1 falsified: hostile workload gained authority; output redacted")

    assert result.returncode == 1, (
        f"Expected hostile workload denial (exit 1), got {result.returncode}; output redacted"
    )

    report = json.loads(result.stdout)
    assert report["status"] == "SECURE"
    assert report["reason"] == "isolation_held"
    assert "CEM1_TEST_SECRET" not in report["report"]["suspicious_env_vars"]
