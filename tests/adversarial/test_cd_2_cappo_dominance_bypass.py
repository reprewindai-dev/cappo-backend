from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    MountScope,
)
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.db.base import Base
from cappo_backend.models.capability_lease import CapabilityLease
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.security.biscuit import mint_biscuit_capability


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    from cappo_backend.security.merkle_ops import seed_merkle_sequence
    with sessionmaker(bind=engine)() as tmp_session:
        seed_merkle_sequence(tmp_session)
        tmp_session.commit()
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_cd_2_cappo_dominance_repair(db_session):
    pkg = CapabilityPackage(
        id="test.bypass.pkg@v1",
        family="test",
        title="test",
        purpose="test",
        reads=[],
        writes=["fs.write"],
    )
    scope = MountScope(workspace="ws", project="pj", writes=["fs.write"])

    class DummyAnchor:
        def anchor(self, *args, **kwargs):
            from cappo_backend.capability_mount.service import AnchorResult
            return AnchorResult("confirmed", "dummy_anchor", "detail")

    registry = MountRegistry(db_session, anchor=DummyAnchor())
    registry.register_package(pkg)

    mount, token = registry.mounter.mount(pkg, scope)

    # Mint a real biscuit token so P3 strict enforcement (No Biscuit => DENY) is satisfied.
    _biscuit_b64 = mint_biscuit_capability(
        caller_spiffe_id="spiffe://test/sub",
        executor_spiffe_id="spiffe://test/exec",
        capability_id="test-cap",
        reads=[],
        writes=["fs.write"],
        execution_id=token.execution_id,
        ttl_seconds=token.ttl_seconds,
    )
    token = token.model_copy(update={"biscuit_token": _biscuit_b64})

    lease = CapabilityLease(
        lease_id="test-lease",
        mount_id=mount.id,
        capability_id="test-cap",
        policy_version="1",
        execution_identity="test-id",
        subject_spiffe_id="spiffe://test/sub",
        executor_spiffe_id="spiffe://test/exec",
        biscuit_hash="test-hash",
        expires_at=token.expires_at,
        authority_epoch=1,
        offline_enabled=True,
        offline_budget=10,
        offline_side_effect_limit=10,
        _allowed_actions_json='["fs.write"]',
    )
    db_session.add(lease)

    row = CapabilityMount(
        mount_id=mount.id,
        token_id=token.token_id,
        token_nonce=token.nonce,
        owner_principal="spiffe://test/sub",
        owner_workspace="ws",
        mount_json=mount.model_dump(mode="json"),
        token_json=token.model_dump(mode="json"),
        issued_at=datetime.now(timezone.utc),
        expires_at=token.expires_at,
        terminated=False,
        nonce_consumed=False,
    )
    db_session.add(row)
    db_session.commit()
    
    record = registry._record(row)
    binding = record.binding

    consequence_occurred = False
    def consequence():
        nonlocal consequence_occurred
        consequence_occurred = True
        return "success"
    
    result = binding.consequence("fs.write", consequence)
    
    assert consequence_occurred is True
    assert result == "success"
    
    db_session.refresh(row)
    db_session.refresh(lease)
    
    assert row.nonce_consumed is True, "Repair: Nonce MUST be consumed!"
    assert lease.offline_budget == 10, "Repair: Online execution leaves budget unchanged!"
