import httpx
import uuid
import datetime
import json
import sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ed25519

KMS_KEYS_FILE = Path.home() / ".cappo_mock_kms_keys.json"
keys = json.loads(KMS_KEYS_FILE.read_text())
kid, hex_bytes = list(keys.items())[1]
private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_bytes))

import jwt
now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
claims = {
    "iss": "cappo.veklom.com", "aud": "sandbox_file_append",
    "iat": now, "exp": now + 300, "jti": uuid.uuid4().hex,
    "sub": "workspace:ws_local_n8n16_certification", "lease_id": "test_lease",
    "execution_id": "test_exec", "workflow_id": "W6r3rV1OqR",
    "allowed_actions": ["fs:append"], "allowed_resources": ["sandbox-file:C:/Users/antho/.windsurf/cappo-backend/scratch/n8n17/n8n_governed_append.jsonl"],
    "budget": {"currency": "USD_CENT", "max": 1},
}
token = jwt.encode(claims, private_key, algorithm="EdDSA", headers={"kid": kid})

resp = httpx.post("http://127.0.0.1:8099/connectors/sandbox-file-append", 
                  headers={"Authorization": f"Bearer {token}"},
                  json={"action": "fs:append", "content": "test"})
print(resp.status_code, resp.text)
