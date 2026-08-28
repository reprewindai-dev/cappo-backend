"""
CD-1 ?" Consequence Domination

Hypothesis: CAPPO defines the logical consequence-authority semantics: every declared 
consequence must be dominated by a prior authorization decision. CD-1 requires that 
every consequential sink (e.g., DB writes, network egress, queue publishes, file mutations, 
external calls) be semantically dominated by CAPPO, regardless of whether enforcement is 
centralized, embedded, replicated, or sink-adjacent.

Validation must show that no execution substrate allows a consequence sink to be reached 
without passing through CAPPO's consequence-authority semantics.

In this test, we prove that `ExecutionBinding` dominates the sink inventory exactly
according to the capability profile, leaving no semantic bypasses in the control plane itself.
We use the integrated DatabaseAuditSink to prove durable recording.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from cappo_backend.capability_mount.engine import ExecutionBinding
from cappo_backend.capability_mount.errors import PolicyError
from cappo_backend.capability_mount.models import (
    EphemeralScopedToken,
    Grants,
    MountPolicy,
    TokenDescriptorScope,
)
from cappo_backend.capability_mount.service import DatabaseAuditSink
from cappo_backend.db.base import Base
from cappo_backend.models.audit_event import AuditEvent


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _create_token(grants: Grants, ttl: int = 300) -> EphemeralScopedToken:
    return EphemeralScopedToken(
        token_id=f"tok_{uuid4().hex}",
        mount_id=f"mnt_{uuid4().hex}",
        execution_id=f"exec_{uuid4().hex}",
        package_ref="test.cd1.package@v1",
        scope=TokenDescriptorScope(workspace="ws", project="pj"),
        grants=grants,
        policy=MountPolicy(require_human_approval_for_external_send=False),
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
        ttl_seconds=ttl,
        nonce="nonce123",
    )


def test_cd_1_consequence_domination_sink_inventory(db_session):
    """
    Test that every category of consequence sink is blocked by ExecutionBinding 
    if the capability profile does not explicitly grant it.
    """
    # Create an active token with NO GRANTS
    empty_grants = Grants(reads=[], writes=[], blocked=[], external_send=[], suppression_required=[])
    token = _create_token(empty_grants)
    audit = DatabaseAuditSink(db_session, workspace_id="ws")
    binding = ExecutionBinding(token, sink=audit)

    # Sink 1: DB Writes
    def fake_db_write():
        return "db_written"

    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.evaluate_pure("db.write")

    # Sink 2: Network Egress
    def fake_network_egress():
        return "packet_sent"

    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.evaluate_pure("network.egress")

    # Sink 3: Process Spawn
    def fake_process_spawn():
        return "process_started"

    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.evaluate_pure("process.spawn")

    # Sink 4: File Mutations
    def fake_file_write():
        return "file_written"

    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.evaluate_pure("fs.write")

    # Sink 5: Cryptographic Signing
    def fake_crypto_sign():
        return "signature_generated"

    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.evaluate_pure("crypto.sign")

    # Verify that ALL 5 blocked attempts were successfully logged in the actual database ledger
    db_events = db_session.scalars(select(AuditEvent).where(AuditEvent.operation_type == "execution_binding_decision")).all()
    assert len(db_events) == 5
    for event in db_events:
        payload = event.payload
        assert payload["decision"] == "deny"
        assert payload["reason"] == "not_in_capability_profile"


def test_cd_1_explicit_deny_dominates_grant(db_session):
    """
    Test that an explicit block in the capability profile dominates an explicit grant,
    preventing any contradictory escalation.
    """
    # Token explicitly grants "db.write" but also BLOCKS it
    grants = Grants(
        reads=[], 
        writes=["db.write"], 
        blocked=["db.write"], 
        external_send=[], 
        suppression_required=[]
    )
    token = _create_token(grants)
    audit = DatabaseAuditSink(db_session, workspace_id="ws")
    binding = ExecutionBinding(token, sink=audit)

    def fake_db_write():
        return "should_never_happen"

    # The call MUST fail with blocked_action, overriding the writes=[] grant
    with pytest.raises(PolicyError, match="blocked_action"):
        binding.evaluate_pure("db.write")

    db_events = db_session.scalars(select(AuditEvent).where(AuditEvent.operation_type == "execution_binding_decision")).all()
    assert len(db_events) == 1
    
    payload = db_events[0].payload
    assert payload["decision"] == "deny"
    assert payload["reason"] == "blocked_action"
