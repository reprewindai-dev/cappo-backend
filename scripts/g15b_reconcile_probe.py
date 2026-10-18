import datetime
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import ed25519

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cappo_backend.db.session import SessionLocal
from cappo_backend.models.consequence_execution import (
    ConsequenceExecutionEvent,
    build_proof_subject_hash,
)


def ts():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def main():
    print("====================================================")
    print("G1.5b RECONNECT & RECONCILIATION PROBE")
    print("====================================================")

    # Setup
    TARGET_PATH = Path(__file__).resolve().parents[1] / 'scratch' / 'n8n17' / 'n8n_governed_append.jsonl'
    from cappo_backend.execution.sandbox_file_connector import SandboxFileAppendConnector
    resource = SandboxFileAppendConnector.canonicalize_resource(TARGET_PATH)
    
    KMS_KEYS_FILE = Path.home() / '.cappo_mock_kms_keys.json'
    keys = json.loads(KMS_KEYS_FILE.read_text())
    kid, hex_bytes = list(keys.items())[1]
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_bytes))

    now = int(time.time())
    exec_id = 'exec-recon-' + uuid.uuid4().hex[:8]
    workspace_id = 'workspace:ws_reconcile_test'
    op_id = 'op-' + exec_id

    # Create the offline token
    claims = {
        'iss': 'cappo.veklom.com', 'aud': 'sandbox_file_append',
        'sub': workspace_id, 'workspace_id': workspace_id, 'lease_id': 'lease-recon',
        'workflow_id': 'W_recon', 'allowed_actions': ['fs:append'],
        'allowed_resources': [resource], 'budget': {'currency': 'USD_CENT', 'max': 1},
        'execution_id': exec_id, 'iat': now, 'exp': now + 300, 'jti': uuid.uuid4().hex
    }
    token = pyjwt.encode(claims, private_key, algorithm='EdDSA', headers={'kid': kid})

    # Token 0: Valid Initial (to cache the key)
    exec_init = 'exec-init-' + uuid.uuid4().hex[:8]
    c_init = claims.copy()
    c_init.update({'execution_id': exec_init, 'jti': uuid.uuid4().hex})
    token_init = pyjwt.encode(c_init, private_key, algorithm='EdDSA', headers={'kid': kid})

    # STEP 0: Cache the key while online
    print(f"\n[{ts()}] STEP 0: Online pre-flight to cache KMS key...")
    try:
        res = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                        json={'action': 'fs:append', 'content': f'INIT CACHE {exec_init}'},
                        headers={'Authorization': f'Bearer {token_init}'})
        if res.status_code != 200: sys.exit(f"Init failed. {res.text}")
    except httpx.ConnectError:
        sys.exit("Target connector not running!")

    # Kill CAPPO
    print(f"\n[{ts()}] Killing CAPPO Authority on :8002...")
    try:
        out = subprocess.check_output('netstat -ano | findstr :8002', shell=True).decode()
        for line in out.splitlines():
            if 'LISTENING' in line:
                pid = line.strip().split()[-1]
                subprocess.run(f'taskkill /F /PID {pid}', shell=True)
                print(f"[{ts()}] Killed CAPPO PID {pid}")
    except Exception as e:
        print(f"[{ts()}] Failed to kill CAPPO: {e}")
    time.sleep(2)

    # STEP 1: Simulate CAPPO dispatching and losing connection (OUTCOME_UNKNOWN)
    print(f"\n[{ts()}] STEP 1: Simulating CAPPO disconnection (Inserting OUTCOME_UNKNOWN)...")
    db = SessionLocal()
    ce_auth = ConsequenceExecutionEvent(
        event_id=f"evt_{uuid.uuid4().hex}", operation_id=op_id, intent_hash="mock_intent",
        state="authorized", version=1, mount_id="mount_1", execution_id=exec_id,
        principal=workspace_id, action="fs:append", resource=resource,
        completion_proof_type="optimistic_claim", proof_subject_hash="mock_hash"
    )
    ce_start = ConsequenceExecutionEvent(
        event_id=f"evt_{uuid.uuid4().hex}", operation_id=op_id, intent_hash="mock_intent",
        state="started", version=2, mount_id="mount_1", execution_id=exec_id,
        principal=workspace_id, action="fs:append", resource=resource,
        completion_proof_type="optimistic_claim", proof_subject_hash="mock_hash_2"
    )
    ce_unk = ConsequenceExecutionEvent(
        event_id=f"evt_{uuid.uuid4().hex}", operation_id=op_id, intent_hash="mock_intent",
        state="outcome_unknown", version=3, mount_id="mount_1", execution_id=exec_id,
        principal=workspace_id, action="fs:append", resource=resource,
        completion_proof_type="outcome_uncertain", proof_subject_hash="mock_hash_3"
    )
    db.add_all([ce_auth, ce_start, ce_unk])
    db.commit()
    print(f"[{ts()}] Central truth state is now OUTCOME_UNKNOWN.")

    # STEP 2: The execution actually succeeds offline at the Target Connector
    print(f"\n[{ts()}] STEP 2: Target Connector successfully executes offline...")
    res = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                     json={'action': 'fs:append', 'content': f'RECONCILE {exec_id}'},
                     headers={'Authorization': f'Bearer {token}'})
    if res.status_code != 200: sys.exit(f"Target execution failed: {res.text}")
    print(f"[{ts()}] Target Connector executed successfully. Consequence created.")
    
    def get_count():
        if not TARGET_PATH.exists(): return 0
        return sum(1 for line in TARGET_PATH.read_text().splitlines() if exec_id in line)
    
    c_before = get_count()
    if c_before != 1: sys.exit(f"Expected 1 consequence, got {c_before}")

    # STEP 3: CAPPO comes back and reconciles
    print(f"\n[{ts()}] STEP 3: CAPPO Reconciler runs (queries Target Connector via CAPPO)...")
    try:
        # Give CAPPO time to wake up if it's restarted
        # Actually in this probe we killed it, so we need to restart it first!
        print(f"[{ts()}] Restarting CAPPO Authority...")
        cappo_proc = subprocess.Popen("set OLLAMA_KEEP_ALIVE=600 && uv run uvicorn cappo_backend.main:app --host 127.0.0.1 --port 8002", shell=True)
        time.sleep(5) # Wait for it to bind
        
        print(f"[{ts()}] Triggering CAPPO Reconciler endpoint...")
        res_recon = httpx.post(
            f'http://127.0.0.1:8002/api/v1/reconcile/{exec_id}', 
            headers={'Authorization': f'Bearer {token}'},
            timeout=10.0
        )
        if res_recon.status_code != 200: sys.exit(f"Reconciliation trigger failed: {res_recon.text}")
        recon_data = res_recon.json()
        print(f"[{ts()}] CAPPO genuinely fetched and reconciled the receipt! Result: {recon_data.get('status')}")
    finally:
        pass

    # STEP 4: Verify the central projection accepts it and no duplicate consequence occurred
    print(f"\n[{ts()}] STEP 4: Verify Finality and Duplicate Suppression...")
    final_state = db.query(ConsequenceExecutionEvent).filter_by(execution_id=exec_id).order_by(ConsequenceExecutionEvent.version.desc()).first()
    print(f"[{ts()}] Projection Status: {final_state.state}")
    if final_state.state != "reconciled_succeeded": sys.exit("Projection is not reconciled_succeeded")
    
    # Try an exact replay from orchestrator
    print(f"[{ts()}] Orchestrator retries execution blindly...")
    res_retry = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                     json={'action': 'fs:append', 'content': f'RECONCILE {exec_id}'},
                     headers={'Authorization': f'Bearer {token}'})
    
    c_after = get_count()
    if res_retry.status_code == 200 and c_after == 1:
        print(f"[{ts()}] SUCCESS! Retry returned HTTP 200 (idempotent receipt) but physical consequence remained exactly {c_after}.")
        print(f"[{ts()}] PASS G1.5b: Reconnect/Reconciliation without duplicate consequence.")
    else:
        sys.exit(f"FAIL G1.5b. Status: {res_retry.status_code}, Count: {c_after}")
        
    print("\n====================================================")
    print("G1.5b RECONCILIATION PROBE COMPLETED SUCCESSFULLY.")
    print("====================================================")
    
    # Cleanup CAPPO
    if 'cappo_proc' in locals():
        subprocess.run(f'taskkill /F /T /PID {cappo_proc.pid}', shell=True)

if __name__ == '__main__':
    main()
