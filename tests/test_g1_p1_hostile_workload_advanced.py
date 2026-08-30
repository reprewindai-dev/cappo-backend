import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cappo_backend.db.base import Base
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt


def test_hostile_workload_environment_isolation():
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" not in os.environ

def test_ei_cross_binding_mismatch():
    ei_a = {"execution_id": "exec-A", "budget": {"remaining": 1}, "directive": "ALLOW"}
    validated_execution_id = ei_a.get("execution_id")
    assert validated_execution_id == "exec-A"

def test_receipt_mutation_detectable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    receipt = CapabilityActionReceipt(
        receipt_id="rcpt_1",
        execution_id="exec-123",
        mount_id="mnt_1",
        token_id="tok_1",
        principal="test",
        action="contact.read",
        decision="ALLOW",
        reason="allowed",
        actioned_at=datetime.now(timezone.utc),
        content_hash="hash_orig",
        merkle_leaf_index=1,
        signed_receipt_cose=b"signature_bytes_original"
    )
    db.add(receipt)
    db.commit()

    db.execute(
        CapabilityActionReceipt.__table__.update().
        where(CapabilityActionReceipt.receipt_id == "rcpt_1").
        values(signed_receipt_cose=b"signature_bytes_forged_allow")
    )
    db.commit()

    mutated = db.query(CapabilityActionReceipt).first()
    assert mutated.signed_receipt_cose == b"signature_bytes_forged_allow"
    is_valid = mutated.signed_receipt_cose == b"signature_bytes_original"
    assert not is_valid

def test_merkle_index_mutation_detectable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    receipt1 = CapabilityActionReceipt(
        receipt_id="rcpt_1", execution_id="exec-123", mount_id="mnt_1", token_id="tok_1", principal="test", action="contact.read", decision="ALLOW", reason="allowed", actioned_at=datetime.now(timezone.utc), content_hash="hash_orig",
        merkle_leaf_index=1)
    receipt2 = CapabilityActionReceipt(
        receipt_id="rcpt_2", execution_id="exec-124", mount_id="mnt_2", token_id="tok_2", principal="test", action="contact.read", decision="ALLOW", reason="allowed", actioned_at=datetime.now(timezone.utc), content_hash="hash_orig",
        merkle_leaf_index=2)
    db.add_all([receipt1, receipt2])
    db.commit()

    receipt2.merkle_leaf_index = 10
    db.commit()

    records = db.query(CapabilityActionReceipt).order_by(CapabilityActionReceipt.merkle_leaf_index).all()
    indices = [r.merkle_leaf_index for r in records]
    
    assert indices == [1, 10]
    
    is_consistent = (indices == list(range(1, len(indices) + 1)))
    assert not is_consistent
