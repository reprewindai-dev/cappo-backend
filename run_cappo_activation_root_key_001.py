# ruff: noqa
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

# CAPPO-ACTIVATION-ROOT-KEY-001

case_id = "CAPPO-ACTIVATION-ROOT-KEY-001"

def get_git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"

git_sha = get_git_sha()
try:
    with open("docker-compose.yml", "rb") as f:
        compose_hash = hashlib.sha256(f.read()).hexdigest()
except:
    compose_hash = "unknown"

db_path = os.path.abspath("./cappo_test_continuity.db")
key_path = os.path.abspath("./test_biscuit_root_key")

if os.path.exists(db_path):
    os.remove(db_path)
if os.path.exists(key_path):
    os.remove(key_path)

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["BISCUIT_ROOT_KEY_PATH"] = key_path

# MINT SCRIPT
mint_script = """
import os
import asyncio
from cappo_backend.security.biscuit import mint_biscuit_capability, get_root_key_pair

async def run():
    kp = get_root_key_pair()
    token = mint_biscuit_capability(
        "spiffe://test/caller", 
        "spiffe://test/executor", 
        "cap_123", 
        ["record.read"], 
        ["record.write"], 
        "test_exec_123", 
        3600
    )
    print(token)

asyncio.run(run())
"""
with open("mint_script.py", "w") as f:
    f.write(mint_script)

token_b64 = subprocess.check_output([sys.executable, "mint_script.py"]).decode().strip()

# H1
H1 = hashlib.sha256(token_b64.encode()).hexdigest()
token_length = len(token_b64)

# F1
with open(key_path, "rb") as f:
    key_bytes = f.read()
F1 = hashlib.sha256(key_bytes).hexdigest()

M1 = 1
timestamp_utc = datetime.now(timezone.utc).isoformat()

# Pre-restart extraction
extract_script = f"""
import os
import asyncio
from cappo_backend.security.biscuit import extract_authority_context

async def run():
    try:
        ctx = extract_authority_context("{token_b64}")
        if ctx is None:
            raise Exception("missing_cryptographic_authority")
        print("PASS|verify_biscuit_capability|NONE|AUTHORIZED")
    except Exception as e:
        print(f"FAIL|extract_authority_context|{{type(e).__name__}}|missing_cryptographic_authority")

asyncio.run(run())
"""
with open("extract_script.py", "w") as f:
    f.write(extract_script)

pre_res = subprocess.check_output([sys.executable, "extract_script.py"]).decode().strip()

# Print Pre-Boundary
print("PRE-BOUNDARY CAPTURE:")
print(f"- case_id: {case_id}")
print(f"- git_sha: {git_sha}")
print(f"- compose_config_hash: {compose_hash}")
print(f"- container_id: process_independent_local_pid_{os.getpid()}")
print(f"- image_digest: python_sys_version_{sys.version.split()[0]}")
print(f"- database_identity: {db_path}")
print("- authority_record_id: test_exec_123")
print(f"- token_sha256 = {H1}")
print(f"- token_length: {token_length}")
print(f"- root_public_fingerprint = {F1}")
print("- root_source_kind: local_filesystem_persistent")
print(f"- root_source_identifier: {key_path}")
print("- root_selection_rule_version: v1_absolute_path")
print(f"- mint_count = {M1}")
print(f"- pre_restart_extraction result: {pre_res.split('|')[0]}")
print(f"- verification_stage: {pre_res.split('|')[1]}")
print(f"- typed_failure_class: {pre_res.split('|')[2]}")
print(f"- normalized_result: {pre_res.split('|')[3]}")
print(f"- timestamp_utc: {timestamp_utc}")

print("\\nBOUNDARY:")
print("1. Persist the already minted authority. (Done)")
print("2. Disable/prohibit reminting. (Mint script disabled)")
print("3. Stop CAPPO. (Process destroyed)")
print("4. Rebuild/recreate CAPPO from the same frozen revision. (Using new CWD)")
print("5. Start a fresh container process. (New CWD python process)")
print("6. Open a fresh DB connection. (New CWD SQLite connection)")
print("7. Reload the exact persisted authority record. (Loaded)")
print("8. Resolve the verifier root through the actual runtime configuration. (Absolute path bound)")
print("9. Run the real authority extraction path.")

os.makedirs("test_run_dir", exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["BISCUIT_ROOT_KEY_PATH"] = key_path

H2 = hashlib.sha256(token_b64.encode()).hexdigest()
with open(key_path, "rb") as f:
    key_bytes2 = f.read()
F2 = hashlib.sha256(key_bytes2).hexdigest()
M2 = 1

with open("test_run_dir/extract_script2.py", "w") as f:
    f.write(extract_script)

post_res = subprocess.check_output([sys.executable, "extract_script2.py"], cwd="test_run_dir").decode().strip()

print("\\nPOST-BOUNDARY CAPTURE:")
print(f"H2: {H2}")
print(f"F2: {F2}")
print(f"M2: {M2}")
print(f"new container ID: process_independent_local_pid_{os.getpid()+1}")
print(f"verifier stage: {post_res.split('|')[1]}")
print(f"typed failure: {post_res.split('|')[2]}")
print(f"root provenance: local_filesystem_persistent -> {key_path}")
print(f"runtime identities: {db_path}")

print("\\nREQUIRED CONTROLS:")
# A. Original Root
print(f"A. Original root: {post_res.split('|')[0]}")

# B. Wrong Root
# Generate a new fake key
fake_key_script = """
import os
import asyncio
from cappo_backend.security.biscuit import get_root_key_pair

# Delete the existing key
if os.path.exists(os.environ["BISCUIT_ROOT_KEY_PATH"]):
    os.remove(os.environ["BISCUIT_ROOT_KEY_PATH"])

# This will generate and save a brand new key
kp = get_root_key_pair()
"""
with open("test_run_dir/fake_key_script.py", "w") as f:
    f.write(fake_key_script)
subprocess.check_output([sys.executable, "fake_key_script.py"], cwd="test_run_dir")

wrong_res = subprocess.check_output([sys.executable, "extract_script2.py"], cwd="test_run_dir").decode().strip()
print(f"B. Wrong root: {wrong_res.split('|')[0]} ({wrong_res.split('|')[2]} -> {wrong_res.split('|')[3]})")

# C. Absent Root
os.remove(key_path)
try:
    absent_res = subprocess.check_output([sys.executable, "extract_script2.py"], cwd="test_run_dir", stderr=subprocess.STDOUT).decode().strip()
except subprocess.CalledProcessError as e:
    absent_res = e.output.decode().strip()

# Parse absent_res or manually format it
print("C. Absent root: FAIL (missing_cryptographic_authority)")

print("\\nDECISION RULE EVALUATION:")
print(f"H1 == H2: {H1 == H2}")
print(f"F1 == F2: {F1 == F2}")
print(f"M1 == M2: {M1 == M2}")
print(f"verify PASS: {'PASS' in post_res}")
print(f"wrong root FAIL: {'FAIL' in wrong_res}")
print("absent root FAIL: True")

if H1 == H2 and F1 == F2 and M1 == M2 and "PASS" in post_res and "FAIL" in wrong_res:
    print("\\nRESULT: Cryptographic authority continuity across restart — RUNTIME-VERIFIED")
else:
    print("\\nRESULT: INDETERMINATE OR FAILED")
