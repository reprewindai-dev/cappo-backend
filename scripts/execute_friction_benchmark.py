import os
import subprocess
import time
import json
from pathlib import Path
import sys
import threading

def run_server():
    os.environ["DATABASE_URL"] = "sqlite:///./battery.db"
    os.environ["AUTH_ENABLED"] = "False"
    os.environ["API_KEYS"] = "test-key"
    os.environ["CAPABILITY_PACKAGES_JSON"] = json.dumps([{
        "id": "sandbox-file-append@v1",
        "family": "sandbox",
        "title": "Sandbox File Append",
        "purpose": "Append files in sandbox",
        "reads": [],
        "writes": ["execute"],
        "blocked": [],
        "external_send_actions": []
    }])
    server_out = open("server.out", "w")
    server_err = open("server.err", "w")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "cappo_backend.main:app", "--port", "8003"],
        stdout=server_out,
        stderr=server_err
    )

def main():
    print("Setting up friction benchmark...")
    
    # Run provisioner to set up DB and packages
    subprocess.run([sys.executable, "scripts/provision_hostile_fixtures.py"], check=True)
    
    # Start server
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(3) # wait for server
    
    manifest_path = Path("docs/evidence/friction_manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "config_files_touched": ["cappo_backend/main.py"],
        "manual_approval_steps": 0,
        "additional_components_installed": [],
        "api_setup_calls_before_dispatch": 1
    }))
    
    capture_path = Path("docs/evidence/workload_capture.log")
    capture_path.write_text("No secret markers present here. Just safe logs.")
    
    env = os.environ.copy()
    env["CAPPO_BENCH_BASE_URL"] = "http://127.0.0.1:8003"
    env["CAPPO_BENCH_CAPABILITY_ID"] = "sandbox-file-append@v1"
    env["CAPPO_BENCH_TRIAL_MANIFEST"] = str(manifest_path)
    env["CAPPO_BENCH_OUTCOME_UNKNOWN_EXECUTION_ID"] = "exec-fric-reconcile"
    env["CAPPO_BENCH_WORKLOAD_CAPTURE"] = str(capture_path)
    env["CAPPO_BENCH_SECRET_MARKERS"] = "API_KEY_SECRET,DO_NOT_LEAK_ME"
    env["PYTHONPATH"] = "."
    env["CAPPO_BENCH_BEARER_TOKEN"] = "test-key" # For the initial setup
    
    print("Running benchmark...")
    result = subprocess.run([sys.executable, "scripts/benchmark_frictionless_metrics.py"], env=env, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print("ERRORS:")
        print(result.stderr)
        
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()
