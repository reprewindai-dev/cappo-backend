"""
Adversarial tests for the unified physical substrate authority path.
"""

from __future__ import annotations

import json
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.engine import Decision
from cappo_backend.capability_mount.models import MountPolicy, MountScope, CapabilityPackage
from cappo_backend.capability_mount.service import MountRegistry, GOVERNED_COUNTER_PACKAGE, AnchorResult
from cappo_backend.models.capability_lease import CapabilityLease
from cappo_backend.capability_mount.effects import TargetAdapterRegistry, GovernedCounterAdapter
from cappo_backend.db.session import SessionLocal

@pytest.fixture
def db_session() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

@pytest.fixture
def registry(db_session: Session, tmp_path) -> MountRegistry:
    target_adapters = TargetAdapterRegistry()
    target_adapters.register(GovernedCounterAdapter.ref, GovernedCounterAdapter(tmp_path))
    
    class MockAnchor:
        def anchor(self, event_type, **kwargs):
            return AnchorResult("confirmed", anchor_id="mock", detail="mock")

    r = MountRegistry(db=db_session, target_adapters=target_adapters, anchor=MockAnchor())
    r.register_package(GOVERNED_COUNTER_PACKAGE)
    return r

def create_mount(registry: MountRegistry, db: Session, **kwargs):
    package_ref = GOVERNED_COUNTER_PACKAGE.id
    scope = MountScope(workspace="ws_1", project="proj_1")
    policy = MountPolicy()
    ttl = 300
    return registry.request_mount(
        package_ref=package_ref,
        scope=scope,
        role="executor",
        policy=policy,
        ttl_seconds=ttl,
        owner_workspace="ws_1",
        owner_principal="user_foo",
        **kwargs
    )

def test_valid_mount_wrong_boot_instance(registry: MountRegistry, db_session: Session):
    record, anchor, msg = create_mount(
        registry, db_session,
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a"
    )
    db_session.commit()
    assert record is not None, f"Mount failed: {msg}"
    
    # Try to execute with wrong boot instance
    decision, reason, state, payload = registry.execute_consequence(
        mount_id=record.mount.id,
        action="counter.increment",
        token_id=record.token.token_id,
        nonce=record.token.nonce,
        owner_workspace="ws_1",
        owner_principal="user_foo",
        target_ref="activation.governed-counter",
        resource="counter_1",
        arguments={},
        substrate_id="host_a",
        boot_instance_id="boot_2",  # WRONG
        runtime_key_thumbprint="key_a"
    )
    assert decision == Decision.DENY
    assert reason == "boot_instance_id_mismatch"

def test_valid_mount_wrong_runtime_key(registry: MountRegistry, db_session: Session):
    record, anchor, msg = create_mount(
        registry, db_session,
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a"
    )
    db_session.commit()
    
    decision, reason, state, payload = registry.execute_consequence(
        mount_id=record.mount.id,
        action="counter.increment",
        token_id=record.token.token_id,
        nonce=record.token.nonce,
        owner_workspace="ws_1",
        owner_principal="user_foo",
        target_ref="activation.governed-counter",
        resource="counter_1",
        arguments={},
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_b"  # WRONG
    )
    assert decision == Decision.DENY
    assert reason == "runtime_key_thumbprint_mismatch"

def test_host_a_mount_replayed_by_host_b(registry: MountRegistry, db_session: Session):
    record, anchor, msg = create_mount(
        registry, db_session,
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a"
    )
    db_session.commit()
    
    # Host B tries to use it
    decision, reason, state, payload = registry.execute_consequence(
        mount_id=record.mount.id,
        action="counter.increment",
        token_id=record.token.token_id,
        nonce=record.token.nonce,
        owner_workspace="ws_1",
        owner_principal="user_foo",
        target_ref="activation.governed-counter",
        resource="counter_1",
        arguments={},
        substrate_id="host_b",  # WRONG
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a"
    )
    assert decision == Decision.DENY
    assert reason == "substrate_id_mismatch"

def test_reboot_old_mount_reused(registry: MountRegistry, db_session: Session):
    # Same physical host rebooted -> old mount reused -> DENY
    record, anchor, msg = create_mount(
        registry, db_session,
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a"
    )
    db_session.commit()
    
    decision, reason, state, payload = registry.execute_consequence(
        mount_id=record.mount.id,
        action="counter.increment",
        token_id=record.token.token_id,
        nonce=record.token.nonce,
        owner_workspace="ws_1",
        owner_principal="user_foo",
        target_ref="activation.governed-counter",
        resource="counter_1",
        arguments={},
        substrate_id="host_a",
        boot_instance_id="boot_2",  # Rebooted
        runtime_key_thumbprint="key_a"
    )
    assert decision == Decision.DENY
    assert reason == "boot_instance_id_mismatch"

def test_stale_epoch_denied(registry: MountRegistry, db_session: Session):
    record, anchor, msg = create_mount(
        registry, db_session,
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a"
    )
    db_session.commit()
    # Simulate epoch transition
    lease = CapabilityLease(
        lease_id=str(uuid4()),
        mount_id=record.mount.id,
        capability_id="cap_1",
        policy_version="1",
        execution_identity="exec_1",
        subject_spiffe_id="foo",
        executor_spiffe_id="bar",
        biscuit_hash="hash",
        authority_epoch=2,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    db_session.add(lease)
    db_session.commit()
    
    # Execute with stale epoch (1) while active is 2
    decision, reason, state, payload = registry.execute_consequence(
        mount_id=record.mount.id,
        action="counter.increment",
        token_id=record.token.token_id,
        nonce=record.token.nonce,
        owner_workspace="ws_1",
        owner_principal="user_foo",
        target_ref="activation.governed-counter",
        resource="counter_1",
        arguments={},
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a",
        authority_epoch=1  # STALE
    )
    assert decision == Decision.DENY
    assert reason == "stale_authority_epoch"

def test_altered_state_root(registry: MountRegistry, db_session: Session):
    record, anchor, msg = create_mount(
        registry, db_session,
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a",
        state_root="sha256:0000000000000000000000000000000000000000000000000000000000000000"
    )
    db_session.commit()
    
    # Execute with wrong state root
    decision, reason, state, payload = registry.execute_consequence(
        mount_id=record.mount.id,
        action="counter.increment",
        token_id=record.token.token_id,
        nonce=record.token.nonce,
        owner_workspace="ws_1",
        owner_principal="user_foo",
        target_ref="activation.governed-counter",
        resource="counter_1",
        arguments={},
        substrate_id="host_a",
        boot_instance_id="boot_1",
        runtime_key_thumbprint="key_a",
        state_root="sha256:1111111111111111111111111111111111111111111111111111111111111111"
    )
    assert decision == Decision.DENY
    assert reason == "state_root_mismatch"

def test_evidence_falsifier_signature():
    # Executor signs CONSEQUENCE_OBSERVED using its own runtime key -> PGL verifier rejects it.
    from cappo_backend.models.substrate import PGLUnifiedLifecycleEvent, PGLProducer, PGLSubject
    from pydantic import ValidationError
    
    with pytest.raises(ValidationError):
        PGLUnifiedLifecycleEvent(
            schema_version="veklom.pgl.lifecycle.v1",
            event_type="CONSEQUENCE_OBSERVED",
            run_id="run_1234",
            event_id="evt_1234",
            occurred_at=datetime.now(timezone.utc),
            producer=PGLProducer(role="executor", id="executor_a"), # MUST BE OBSERVER OR SYSTEM FOR OBSERVED
            subject=PGLSubject(type="execution", id="exec_1"),
            details={}
        )

