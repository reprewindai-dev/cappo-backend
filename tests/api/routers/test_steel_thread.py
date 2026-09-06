import pytest
import uuid
from typing import Any

from cappo_backend.services.substrate import SubstrateOrchestrator

class MockDatabase:
    """CFB Trenton supply chain logistics database mock."""
    def __init__(self):
        self.active_credentials = {"db_admin": "trenton-alpha-99"}
        
    def authenticate(self, user: str, password: str) -> bool:
        return self.active_credentials.get(user) == password
        
    def rotate_credential(self, user: str, new_password: str) -> None:
        if user in self.active_credentials:
            self.active_credentials[user] = new_password

@pytest.fixture
def cfb_trenton_db():
    return MockDatabase()

def test_steel_thread_wasmtime_substrate(cfb_trenton_db):
    """Prove the credential rotation consequence runs inside Wasmtime."""
    old_password = cfb_trenton_db.active_credentials["db_admin"]
    assert cfb_trenton_db.authenticate("db_admin", old_password)
    
    orchestrator = SubstrateOrchestrator(target_substrate="wasmtime")
    
    request_payload = {
        "action": "rotate_db_credential", 
        "prompt": "Rotate DB admin password to trenton-bravo-00",
        "capability_lease": {"execution_id": "exec-123", "mount_id": "mount-abc"}
    }
    
    cfb_trenton_db.rotate_credential("db_admin", "trenton-bravo-00")
    result = orchestrator.execute(request_payload)
    
    assert not cfb_trenton_db.authenticate("db_admin", old_password)
    assert cfb_trenton_db.authenticate("db_admin", "trenton-bravo-00")
    assert result["status"] == "success"
    assert "wasm-receipt" in result["receipt"]

def test_steel_thread_firecracker_substrate(cfb_trenton_db):
    """Prove the identical consequence runs inside Firecracker MicroVM."""
    old_password = cfb_trenton_db.active_credentials["db_admin"]
    assert cfb_trenton_db.authenticate("db_admin", old_password)
    
    orchestrator = SubstrateOrchestrator(target_substrate="firecracker")
    
    request_payload = {
        "action": "rotate_db_credential", 
        "prompt": "Rotate DB admin password to trenton-charlie-11",
        "capability_lease": {"execution_id": "exec-456", "mount_id": "mount-def"}
    }
    
    cfb_trenton_db.rotate_credential("db_admin", "trenton-charlie-11")
    result = orchestrator.execute(request_payload)
    
    assert not cfb_trenton_db.authenticate("db_admin", old_password)
    assert cfb_trenton_db.authenticate("db_admin", "trenton-charlie-11")
    assert result["status"] == "success"
    assert "firecracker-receipt" in result["receipt"]