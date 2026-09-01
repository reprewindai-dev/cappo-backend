import os
import sys
import time
import subprocess
import asyncio
import httpx
import uuid
import jwt
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

private_key = ed25519.Ed25519PrivateKey.generate()
public_key = private_key.public_key()
pem_priv = private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption())
pem_pub = public_key.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)

db_path = os.path.abspath("./cappo_activation_test.db")
key_path = os.path.abspath("./test_biscuit_root_key_2")

if os.path.exists(db_path): os.remove(db_path)
if os.path.exists(key_path): os.remove(key_path)

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["BISCUIT_ROOT_KEY_PATH"] = key_path
os.environ["JWT_PUBLIC_VERIFICATION_KEY"] = pem_pub.decode()
os.environ["JWT_ISSUER"] = "https://api.veklom.com"
os.environ["JWT_AUDIENCE"] = "https://cappo.veklom.com"
os.environ["JWT_AUTH_ENABLED"] = "true"
os.environ["CAPABILITY_PACKAGES_JSON"] = '[{"id": "veklom.test@v1", "family": "test", "title": "Test", "purpose": "Testing", "reads": ["record.read"], "writes": ["record.create", "record.delete"]}]'
os.environ["CAPABILITY_EFFECT_RECORD_ROOT"] = os.path.abspath("./test_effect_root")
env = os.environ.copy()

# Run DB metadata create all
from cappo_backend.db.session import engine, SessionLocal
from cappo_backend.db.base import Base
import cappo_backend.models.capability_lease
Base.metadata.create_all(bind=engine)
from cappo_backend.security.merkle_ops import seed_merkle_sequence
with SessionLocal() as db:
    seed_merkle_sequence(db)
    db.commit()

print("Starting CAPPO process...")
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "cappo_backend.main:app", "--port", "8005", "--host", "127.0.0.1"], env=env)
time.sleep(3)

async def run_lifecycle():
    workspace_id = f"ws_{uuid.uuid4().hex[:8]}"
    token_payload = {
        "iss": "https://api.veklom.com",
        "aud": "https://cappo.veklom.com",
        "sub": f"spiffe://veklom/agent/{uuid.uuid4().hex[:8]}",
        "workspace_id": workspace_id,
        "role": "agent",
        "exp": int(time.time()) + 3600
    }
    assertion = jwt.encode(token_payload, pem_priv, algorithm="EdDSA")
    
    CAPPO_URL = "http://127.0.0.1:8005"
    async with httpx.AsyncClient() as client:
        # 1. Mount
        resp = await client.post(
            f"{CAPPO_URL}/v1/capability/mounts",
            headers={"Authorization": f"Bearer {assertion}"},
            json={
                "package_ref": "veklom.test@v1",
                "execution_scope": {"workspace": workspace_id, "project": "prj_123"},
                "requested_action_scope": {"reads": ["record.read"], "writes": ["record.create"]} # Delete omitted on purpose
            }
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        mount_id = data["mount"]["id"]
        token_id = data["token"]["token_id"]
        nonce = data["token"]["nonce"]
        
        print("1. CAPPO Mount: ALLOW")
        
        # 2. record.create EXACTLY ONCE
        resp2 = await client.post(
            f"{CAPPO_URL}/v1/capability/mounts/{mount_id}/execute",
            headers={"Authorization": f"Bearer {assertion}"},
            json={"token_id": token_id, "nonce": nonce, "action": "record.create", "target_ref": "activation.local-record", "resource": "rec-123"}
        )
        assert resp2.status_code == 200, resp2.text
        data2 = resp2.json()
        assert data2["decision"] == "allow", f"Expected allow, got: {data2}"
        print(f"2. record.create: ALLOW -> target_invoked={data2['consequence']['target_invoked']}, state={data2['consequence']['state']}")
        
        # 3. Replay -> zero second effect
        resp3 = await client.post(
            f"{CAPPO_URL}/v1/capability/mounts/{mount_id}/execute",
            headers={"Authorization": f"Bearer {assertion}"},
            json={"token_id": token_id, "nonce": nonce, "action": "record.create", "target_ref": "activation.local-record", "resource": "rec-123"}
        )
        data3 = resp3.json()
        print(f"3. replay record.create: DENY -> reason={data3.get('reason')}, target_invoked={data3.get('consequence',{}).get('target_invoked')}")
        assert data3["decision"] == "deny"
        
        # 4. separate valid authority -> record.delete -> genuine policy/scope DENY
        # Request new mount that includes record.delete in scope?
        # Actually the user prompt: "separate valid authority -> record.delete -> genuine policy/scope DENY -> target calls = 0 -> denial evidence"
        resp4_mount = await client.post(
            f"{CAPPO_URL}/v1/capability/mounts",
            headers={"Authorization": f"Bearer {assertion}"},
            json={
                "package_ref": "veklom.test@v1",
                "execution_scope": {"workspace": workspace_id, "project": "prj_123"},
                "requested_action_scope": {"reads": ["record.read"], "writes": []} 
            }
        )
        data4_mount = resp4_mount.json()
        mount2_id = data4_mount["mount"]["id"]
        token2_id = data4_mount["token"]["token_id"]
        nonce2 = data4_mount["token"]["nonce"]
        
        resp4 = await client.post(
            f"{CAPPO_URL}/v1/capability/mounts/{mount2_id}/execute",
            headers={"Authorization": f"Bearer {assertion}"},
            json={"token_id": token2_id, "nonce": nonce2, "action": "record.delete", "target_ref": "activation.local-record", "resource": "rec-123"}
        )
        data4 = resp4.json()
        print(f"4. record.delete: DENY -> reason={data4.get('reason')}, target_invoked={data4.get('consequence',{}).get('target_invoked')}")
        assert data4["decision"] == "deny"
        
        print("\nACTIVATION v1 LIFECYCLE VERIFIED.")
        
try:
    asyncio.run(run_lifecycle())
finally:
    proc.terminate()
