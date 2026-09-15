import os
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from cappo_backend.state.virtual_db import VeklomVirtualDatabase, AuthorizationError
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.capability_lease import CapabilityLease, LeaseState

@pytest.fixture
def temp_db_path(tmp_path):
    return str(tmp_path / "test_reflux.db")

def test_veklom_virtual_db_reflux_osmosis(temp_db_path):
    vdb = VeklomVirtualDatabase(temp_db_path)
    
    valid_ei = ExecutionIdentity(
        ei_id="test_ei",
        run_id="test_run",
        tenant_id="test_tenant",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        revoked=False
    )
    
    conn = vdb.connect(valid_ei)
    
    # Create table
    conn.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, value TEXT)")
    
    # Generate thousands of state mutations
    with conn:
        for i in range(10000):
            conn.execute("INSERT INTO state (value) VALUES (?)", (f"state_{i}",))
            
    # Verify records
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM state")
    count = cursor.fetchone()[0]
    assert count == 10000
    conn.close()

def test_veklom_virtual_db_invalid_authority(temp_db_path):
    vdb = VeklomVirtualDatabase(temp_db_path)
    
    # Revoked EI
    revoked_ei = ExecutionIdentity(
        ei_id="revoked_ei",
        run_id="test_run",
        tenant_id="test_tenant",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        revoked=True
    )
    
    with pytest.raises(AuthorizationError, match="ExecutionIdentity is revoked"):
        vdb.connect(revoked_ei)
        
    # Expired EI
    expired_ei = ExecutionIdentity(
        ei_id="expired_ei",
        run_id="test_run",
        tenant_id="test_tenant",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        revoked=False
    )
    
    with pytest.raises(AuthorizationError, match="ExecutionIdentity is expired"):
        vdb.connect(expired_ei)
        
    # Invalid CapabilityLease
    revoked_lease = CapabilityLease(
        lease_id="test_lease",
        lease_state=LeaseState.REVOKED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    with pytest.raises(AuthorizationError, match="CapabilityLease is revoked"):
        vdb.connect(revoked_lease)
        
    expired_lease = CapabilityLease(
        lease_id="test_lease_2",
        lease_state=LeaseState.ACTIVE.value,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10)
    )
    with pytest.raises(AuthorizationError, match="CapabilityLease is expired"):
        vdb.connect(expired_lease)
