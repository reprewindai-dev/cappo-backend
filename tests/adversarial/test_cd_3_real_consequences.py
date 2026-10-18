import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cappo_backend.capability_mount.engine import PolicyError
from cappo_backend.capability_mount.models import CapabilityPackage, MountScope
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.models.capability_lease import CapabilityLease
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.security.biscuit import mint_biscuit_capability


class DummyAnchor:
    def anchor(self, *args, **kwargs):
        from cappo_backend.capability_mount.service import AnchorResult
        return AnchorResult('confirmed', 'dummy', 'detail')

class DummyLedger:
    record_called = False
    def append(self, *args, **kwargs):
        self.record_called = True

def _setup_mount(db, writes=[]):
    registry = MountRegistry(db, anchor=DummyAnchor())
    registry.ledger = DummyLedger()

    pkg = CapabilityPackage(id="test.pkg@v1", family="test", title="t", purpose="t", reads=[], writes=writes)
    scope = MountScope(workspace="ws", project="pj", writes=writes)
    registry.register_package(pkg)
    mount, token = registry.mounter.mount(pkg, scope)

    # Mint a real biscuit token so P3 strict enforcement (No Biscuit => DENY) is satisfied.
    _biscuit_b64 = mint_biscuit_capability(
        caller_spiffe_id="spiffe://test/sub",
        executor_spiffe_id="spiffe://test/exec",
        capability_id="test-cap",
        reads=[],
        writes=writes,
        execution_id=token.execution_id,
        ttl_seconds=token.ttl_seconds,
    )
    token = token.model_copy(update={"biscuit_token": _biscuit_b64})

    lease = CapabilityLease(
        lease_id="test-lease-" + token.token_id,
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
        _allowed_actions_json='["fs.write"]' if writes else '[]',
    )
    db.add(lease)

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
    db.add(row)
    db.commit()
    
    record = registry._record(row)
    return row, lease, record.binding

def test_real_fs_consequence_bypass_attempt(db):
    _, _, binding = _setup_mount(db, writes=["fs.write"])

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "bypass.txt"
        
        with pytest.raises(TypeError):
            binding.evaluate_pure("fs.write", lambda: test_file.write_text("BYPASS"))
            
        assert not test_file.exists(), "Filesystem mutation occurred despite missing callback support!"

def test_real_fs_consequence_allow(db):
    row, _, binding = _setup_mount(db, writes=["fs.write"])
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "governed.txt"
        
        def do_write():
            test_file.write_text("SUCCESS")
            return "ok"
            
        result = binding.consequence("fs.write", do_write)
        
        assert result == "ok"
        assert test_file.exists()
        assert test_file.read_text() == "SUCCESS"
        
        db.refresh(row)
        assert row.nonce_consumed is True

def test_real_fs_consequence_deny(db):
    row, _, binding = _setup_mount(db, writes=[])
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "denied.txt"
        
        def do_write():
            test_file.write_text("SHOULD_NOT_HAPPEN")
            return "bad"
            
        with pytest.raises(PolicyError, match="not_in_capability_profile"):
            binding.consequence("fs.write", do_write)
            
        assert not test_file.exists(), "Consequence executed despite CAPPO DENY!"
        
        db.refresh(row)
        assert row.nonce_consumed is False

def test_missing_evaluator_fail_closed(db):
    row, _, binding = _setup_mount(db, writes=["fs.write"])
    binding._cappo_evaluator = None # Force missing evaluator

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "failclosed.txt"
        
        with pytest.raises(PolicyError, match="cappo_evaluator_missing"):
            binding.consequence("fs.write", lambda: test_file.write_text("SHOULD_NOT_HAPPEN"))
            
        assert not test_file.exists()

def test_real_replay_nonce_semantics(db):
    row, _, binding = _setup_mount(db, writes=["fs.write"])

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "replay.txt"
        
        # 1st attempt
        binding.consequence("fs.write", lambda: test_file.write_text("FIRST"))
        assert test_file.read_text() == "FIRST"
        
        # 2nd attempt (Replay)
        with pytest.raises(PolicyError, match="token_replay"):
            binding.consequence("fs.write", lambda: test_file.write_text("SECOND"))
            
        assert test_file.read_text() == "FIRST", "Replay mutation succeeded!"

def test_callback_failure_semantics(db):
    row, _, binding = _setup_mount(db, writes=["fs.write"])
    
    def failing_callback():
        raise RuntimeError("Callback failed halfway!")
        
    with pytest.raises(RuntimeError, match="Callback failed halfway!"):
        binding.consequence("fs.write", failing_callback)
        
    db.refresh(row)
    assert row.nonce_consumed is True

def test_real_consequence_revocation(db):
    row, _, binding = _setup_mount(db, writes=["fs.write"])
    
    # Revoke
    row.terminated = True
    db.commit()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "revoke.txt"
        
        with pytest.raises(PolicyError, match="terminated"):
            binding.consequence("fs.write", lambda: test_file.write_text("SHOULD_NOT_HAPPEN"))
            
        assert not test_file.exists()

def test_real_consequence_expiry(db):
    from datetime import timedelta
    row, _, binding = _setup_mount(db, writes=["fs.write"])
    
    # Expire
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "expire.txt"
        
        with pytest.raises(PolicyError, match="expired"):
            binding.consequence("fs.write", lambda: test_file.write_text("SHOULD_NOT_HAPPEN"))
            
        assert not test_file.exists()

def test_real_consequence_budget_exhaustion(db):
    row, lease, binding = _setup_mount(db, writes=["fs.write"])
    
    # Exhaust budget
    lease.offline_budget = 0
    db.commit()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "budget.txt"
        
        with pytest.raises(PolicyError, match="offline_budget_exhausted"):
            binding.consequence("fs.write", lambda: test_file.write_text("SHOULD_NOT_HAPPEN"))
            
        assert not test_file.exists()
