import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Setup SQLite DB path
db_path = Path("battery.db")
if db_path.exists():
    db_path.unlink()

os.environ["DATABASE_URL"] = "sqlite:///./battery.db"
os.environ["AUTH_ENABLED"] = "False"

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from cappo_backend.db.base import Base
import cappo_backend.models
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.security.biscuit import mint_biscuit_capability

engine = create_engine("sqlite:///./battery.db", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
with engine.connect() as conn:
    conn.execute(text("INSERT OR IGNORE INTO merkle_leaf_sequence (id, next_value) VALUES (1, 0)"))
    conn.commit()

Session = sessionmaker(bind=engine)
db = Session()

def create_fixture(exec_id: str):
    mount_id = f"mount-{uuid.uuid4()}"
    token_id = f"token-{uuid.uuid4()}"
    nonce = f"nonce-{uuid.uuid4()}"
    ws_id = "test-workspace"

    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id="cappo://local/hostile-battery",
        executor_spiffe_id="cappo://local/hostile-battery",
        capability_id="sandbox-file-append@v1",
        reads=[], writes=["execute"], resources=["provider-dispatch"],
        execution_id=exec_id,
        ttl_seconds=300
    )

    mount_json = {
        "id": mount_id, "package_ref": "sandbox-file-append@v1", "role": "test-role",
        "scope": {"workspace": ws_id, "project": "test-project"},
        "token": {"type": "ephemeral_scoped", "ttl_seconds": 600},
        "grants": {"reads": [], "writes": ["execute"], "resources": ["provider-dispatch"], "blocked": [], "external_send": [], "suppression_required": []},
        "policy": {"mode": "draft_only", "default": "deny", "require_human_approval_for_external_send": True, "require_suppression_check": True, "persistent_memory_allowed": False},
        "lifecycle": {"state": "mounted", "unmount_on": ["task_complete", "token_expiry", "explicit_terminate"]}
    }
    
    token_json = {
        "biscuit_token": biscuit_token, "token_id": token_id, "mount_id": mount_id,
        "package_ref": "sandbox-file-append@v1", "scope": {"workspace": ws_id, "project": "test-project"},
        "grants": {"reads": [], "writes": ["execute"], "resources": ["provider-dispatch"], "blocked": [], "external_send": [], "suppression_required": []},
        "policy": {"mode": "draft_only", "default": "deny", "require_human_approval_for_external_send": True, "require_suppression_check": True, "persistent_memory_allowed": False},
        "issued_at": datetime.now(timezone.utc).timestamp(), "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=600)).timestamp(),
        "ttl_seconds": 600, "nonce": nonce, "execution_id": exec_id
    }

    mount = CapabilityMount(
        mount_id=mount_id, token_id=token_id, token_nonce=nonce,
        owner_principal="auth-disabled", owner_workspace=ws_id,
        mount_json=mount_json, token_json=token_json,
        issued_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        terminated=False,
    )
    db.add(mount)
    db.commit()

    return {
        "execution_id": exec_id,
        "mount_id": mount_id,
        "biscuit": biscuit_token
    }

fixtures = {
    "valid": create_fixture("exec-hostile-valid-001"),
    "A": create_fixture("exec-hostile-mismatched-A"),
    "C": create_fixture("exec-hostile-mismatched-C"),
}

out_dir = Path("docs/evidence")
out_dir.mkdir(parents=True, exist_ok=True)
with open(out_dir / "hostile_fixtures.json", "w") as f:
    json.dump(fixtures, f, indent=2)

print("Provisioned hostile fixtures.")
