"""
AI-1 — Authority Ingress Completeness (Out-of-Process Assault)

Hypothesis: Every executable authority unit can be traced to a declared upstream grant.

Attack program:
Spawn an explicitly hostile subprocess (ai1_hostile_workload.py) representing the
execution plane workload. The harness will attempt to pollute the environment with
forbidden secrets (e.g. CAPPO_AUTHORIZATION_OVERRIDE) and leaked database URLs.
The hostile workload will attempt to scrape these inputs and use them to gain 
consequence authority.

Falsifier: Any path where the hostile workload exits with code 0 (meaning it successfully
found and exploited a path to gain authority).
"""

import os
import subprocess
import sys

import pytest

# Define the declared substrate boundary for CEM-1-R1
# In a real environment, this would be `docker run --network none ...`
# Here, it is a local Python subprocess with isolated environment variables.
SUBSTRATE_PYTHON_EXEC = sys.executable
HOSTILE_PAYLOAD = os.path.join(os.path.dirname(__file__), "ai1_hostile_workload.py")

def test_ai_1_out_of_process_assault(tmp_path):
    """
    Spawns the hostile workload and verifies it cannot gain authority.
    """
    # 1. Prepare the hostile environment
    # We pass the real current environment. If the substrate leaks credentials, 
    # the hostile workload will find them and fail the test.
    env = os.environ.copy()
    
    # 2. Spawn the hostile workload
    result = subprocess.run(
        [SUBSTRATE_PYTHON_EXEC, HOSTILE_PAYLOAD],
        env=env,
        capture_output=True,
        text=True
    )
    
    # 3. Evaluate the result
    # The hostile workload returns 0 if it successfully gains authority.
    # It returns 1 if it is successfully isolated.
    
    # If the process exited with 0, it means it found a bypass!
    if result.returncode == 0:
        pytest.fail(f"AI-1 Falsified: Hostile workload successfully gained authority.\nOutput:\n{result.stdout}\n{result.stderr}")
        
    assert result.returncode == 1, f"Expected workload to fail with exit code 1, got {result.returncode}"
    
    # We can inspect the JSON report printed by the workload
    print("Hostile Workload Output:")
    print(result.stdout)
    assert "isolation_held" in result.stdout
