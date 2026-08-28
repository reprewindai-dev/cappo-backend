
import biscuit_auth
from biscuit_auth import Biscuit, KeyPair
from fastapi.testclient import TestClient

from cappo_backend.main import app
from cappo_backend.security.biscuit import (
    attenuate_biscuit_capability,
    get_root_key_pair,
    mint_biscuit_capability,
    verify_biscuit_capability,
)

client = TestClient(app)

def test_hostile_workload_root_key_isolation():
    resp = client.get("/.biscuit_root_key")
    assert resp.status_code in [404, 401]

    resp = client.get("/admin/keys")
    assert resp.status_code in [404, 401]

def test_hostile_workload_unauthorized_root_mint():
    payload = {
        "package_ref": "contact@v1",
        "execution_id": "malicious-exec",
        "role": "workload",
        "ttl_seconds": 3600,
        "execution_scope": {
            "workspace": "acme",
            "project": "default",
            "reads": ["*"],
            "writes": ["*"],
            "blocked": []
        },
        "requested_action_scope": {
            "reads": ["*"],
            "writes": ["*"],
            "blocked": []
        }
    }
    resp = client.post("/mounts", json=payload)
    assert resp.status_code == 401

def test_hostile_workload_scope_widening():
    valid_token = mint_biscuit_capability(
        caller_spiffe_id="spiffe://acme/test",
        executor_spiffe_id="spiffe://acme/agent",
        capability_id="test@v1",
        reads=["contact.read"],
        writes=[],
        execution_id="exec-123",
        ttl_seconds=3600,
        resources=["/contact/123"]
    )

    kp = get_root_key_pair()
    token = Biscuit.from_base64(valid_token, kp.public_key)
    builder = biscuit_auth.BlockBuilder()
    builder.add_code("allowed_action(\"contact.write\");")
    builder.add_code("allowed_resource_child(\"/contact/456\");")
    fake_token = token.append(builder).to_base64()

    result = verify_biscuit_capability(
        token_b64=fake_token,
        executor_spiffe_id="spiffe://acme/agent",
        action="contact.write",
        resource="/contact/456"
    )
    assert not result

def test_hostile_workload_delegation_depth():
    valid_token = mint_biscuit_capability(
        caller_spiffe_id="spiffe://acme/test",
        executor_spiffe_id="spiffe://acme/agent",
        capability_id="test@v1",
        reads=["contact.read"],
        writes=[],
        execution_id="exec-123",
        ttl_seconds=3600
    )

    child_token = attenuate_biscuit_capability(valid_token, reads=["contact.read"])
    assert verify_biscuit_capability(child_token, "spiffe://acme/agent", "contact.read")

    grandchild_token = attenuate_biscuit_capability(child_token, reads=["contact.read"])
    assert not verify_biscuit_capability(grandchild_token, "spiffe://acme/agent", "contact.read")

def test_hostile_workload_verification_key_substitution():
    rogue_kp = KeyPair()
    builder = Biscuit.builder()
    builder.add_code("issuer(\"veklom\");")
    builder.add_code("policy_version(1);")
    builder.add_code("delegation_depth_max(1);")
    builder.add_code("subject(\"spiffe://acme/test\");")
    builder.add_code("check if current_subject($subj), subject($subj) or current_subject(\"any\");")
    builder.add_code("allowed_action(\"contact.read\");")
    builder.add_code("check if current_action($act, $res), allowed_action($act) or current_action(\"terminate\", \"\");")
    builder.add_code("execution_id(\"exec-rogue\");")
    builder.add_code("issued_at(\"2026-01-01T00:00:00Z\");")
    builder.add_code("expires_at(\"2026-12-31T00:00:00Z\");")
    builder.add_code("check if time($time), $time <= \"2026-12-31T00:00:00Z\";")
    
    rogue_token = builder.build(rogue_kp.private_key).to_base64()

    result = verify_biscuit_capability(
        token_b64=rogue_token,
        executor_spiffe_id="spiffe://acme/agent",
        action="contact.read"
    )
    assert not result

