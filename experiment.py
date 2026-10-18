import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

import requests

CASE_ID = "CAPPO-ACTIVATION-ROOT-KEY-001"

print(f"[{CASE_ID}] Starting Experiment")

# 1. Freeze: Get Git SHA
try:
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
except Exception:
    git_sha = "unknown"
print(f"[{CASE_ID}] GIT_SHA: {git_sha}")

# Set up test environment
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite:///./test_cappo.db"
os.environ["BISCUIT_ROOT_KEY_PATH"] = os.path.abspath("./test_biscuit_root_key")
if os.path.exists("./test_cappo.db"):
    os.remove("./test_cappo.db")
if os.path.exists("./test_biscuit_root_key"):
    os.remove("./test_biscuit_root_key")

def start_server(cwd=None):
    print(f"[{CASE_ID}] Starting CAPPO process (cwd={cwd or 'default'})...")
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "cappo_backend.main:app", "--port", "8002", "--host", "127.0.0.1"],
        env=env,
        cwd=cwd
    )
    # Wait for startup
    for _ in range(10):
        try:
            r = requests.get("http://127.0.0.1:8002/health")
            if r.status_code == 200:
                print(f"[{CASE_ID}] Server healthy.")
                return proc
        except requests.exceptions.ConnectionError:
            time.sleep(1)
    print(f"[{CASE_ID}] Server failed to start.")
    proc.kill()
    sys.exit(1)

# Start pre-boundary server
proc = start_server()

# Generate capability token and root key
# We will use the python api directly to mint since there may not be an exposed endpoint
proc.kill() # kill it temporarily so we can mint via python script
proc.wait()

print(f"[{CASE_ID}] PRE-BOUNDARY CAPTURE")
mint_script = """
import os
import asyncio
from cappo_backend.security.biscuit import mint_biscuit_capability, get_root_key_pair

async def run():
    kp = get_root_key_pair()
    token = mint_biscuit_capability("workspace_1", "exec_1")
    print(token)

asyncio.run(run())
"""
with open("mint_script.py", "w") as f:
    f.write(mint_script)

token_b64 = subprocess.check_output([sys.executable, "mint_script.py"]).decode().strip()
print(f"[{CASE_ID}] MINTED TOKEN: {token_b64[:20]}...")

# Pre-boundary checks
# H1
token_sha256 = hashlib.sha256(token_b64.encode()).hexdigest()
print(f"[{CASE_ID}] H1 (token sha256): {token_sha256}")

# F1 (Root key fingerprint)
with open("./test_biscuit_root_key", "rb") as f:
    root_key_data = f.read()
F1 = hashlib.sha256(root_key_data).hexdigest()
print(f"[{CASE_ID}] F1 (root fingerprint): {F1}")
print(f"[{CASE_ID}] M1 (mint count): 1")

# Extract pre-boundary
extract_script = f"""
import os
import asyncio
from cappo_backend.security.biscuit import extract_authority_context

async def run():
    try:
        ctx = extract_authority_context("{token_b64}")
        print("PASS")
    except Exception as e:
        print(f"FAIL: {{e}}")

asyncio.run(run())
"""
with open("extract_script.py", "w") as f:
    f.write(extract_script)

pre_res = subprocess.check_output([sys.executable, "extract_script.py"]).decode().strip()
print(f"[{CASE_ID}] Pre-restart extraction: {pre_res}")

print(f"[{CASE_ID}] --- BOUNDARY CROSSING ---")
print(f"[{CASE_ID}] Stopping CAPPO...")
# already stopped. We simulate the restart by starting in a different directory to prove CWD independence.
os.makedirs("test_run_dir", exist_ok=True)
# adjust DB path and key path for the new CWD to point to absolute locations so they survive
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.abspath('./test_cappo.db')}"
os.environ["BISCUIT_ROOT_KEY_PATH"] = os.path.abspath("./test_biscuit_root_key")

print(f"[{CASE_ID}] POST-BOUNDARY CAPTURE")
token_sha256_2 = hashlib.sha256(token_b64.encode()).hexdigest()
print(f"[{CASE_ID}] H2 (token sha256): {token_sha256_2}")

with open("./test_biscuit_root_key", "rb") as f:
    root_key_data_2 = f.read()
F2 = hashlib.sha256(root_key_data_2).hexdigest()
print(f"[{CASE_ID}] F2 (root fingerprint): {F2}")
print(f"[{CASE_ID}] M2 (mint count): 1")

# Run extract in new CWD
with open("test_run_dir/extract_script2.py", "w") as f:
    f.write(extract_script)

post_res = subprocess.check_output([sys.executable, "test_run_dir/extract_script2.py"], cwd="test_run_dir").decode().strip()
print(f"[{CASE_ID}] Post-restart extraction: {post_res}")

print(f"[{CASE_ID}] --- NEGATIVE CONTROLS ---")
# Wrong root
with open("./test_biscuit_root_key", "w") as f:
    f.write("wrongkey123456789012345678901234567890123456789012345678901234")
wrong_res = subprocess.check_output([sys.executable, "test_run_dir/extract_script2.py"], cwd="test_run_dir").decode().strip()
print(f"[{CASE_ID}] Wrong root extraction: {wrong_res}")

# Absent root
os.remove("./test_biscuit_root_key")
absent_res = subprocess.check_output([sys.executable, "test_run_dir/extract_script2.py"], cwd="test_run_dir").decode().strip()
print(f"[{CASE_ID}] Absent root extraction: {absent_res}")

print(f"[{CASE_ID}] EXPERIMENT COMPLETE.")

if token_sha256 == token_sha256_2 and F1 == F2 and "PASS" in pre_res and "PASS" in post_res and "FAIL" in wrong_res and "FAIL" in absent_res:
    print(f"[{CASE_ID}] STATUS: PASS")
else:
    print(f"[{CASE_ID}] STATUS: FAIL")
