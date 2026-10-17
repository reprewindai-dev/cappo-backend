from __future__ import annotations

from pathlib import Path

from tests.capability_mount.test_execute_consequence import execute_payload, prepare


def test_atomic_handover_chains_history_head_fences_predecessor_and_allows_successor(client, tmp_path: Path) -> None:
    """Proves D0 -> D3 -> D0 atomic incarnation handover:
    - Incarnation 1 (epoch 1) is suspended/superseded by POST /mounts/{id}/handover
    - Handover chains history_head = sha256(prev_history_head:mount_id:1->2:decision_hash:new_token_id)
    - Late request from Incarnation 1 is deterministically refused with stale_authority_epoch (Delta C = 0)
    - Successor Incarnation 2 (epoch 2) executes and applies once (Delta C = 1)
    """
    mount, adapter = prepare(client, tmp_path)
    mount_id = mount["mount"]["id"]
    tok_k1 = mount["token"]

    # Handover from Incarnation 1 (epoch 1) -> Incarnation 2 (epoch 2)
    handover = client.post(
        f"/v1/capability/mounts/{mount_id}/handover",
        json={
            "decision_hash": "dec_sweden_28gco2_window_001",
            "reason": "d3_to_d0_clean_grid_wake",
        },
    )
    assert handover.status_code == 200, handover.text
    h_json = handover.json()
    assert h_json["mount_id"] == mount_id
    assert h_json["incarnation"] == 2
    assert h_json["previous_authority_epoch"] == 1
    assert h_json["authority_epoch"] == 2
    assert h_json["token_id"] != tok_k1["token_id"]
    assert h_json["nonce"] != tok_k1["nonce"]
    assert len(h_json["previous_history_head"]) == 64
    assert len(h_json["history_head"]) == 64
    assert h_json["history_head"] != h_json["previous_history_head"]

    # Incarnation 1 (K1) attempts to execute with its old credentials and epoch 1 -> FENCED
    payload_k1 = execute_payload(mount, operation_id="op_k1_stale", resource="handover-record")
    payload_k1["authority_epoch"] = 1
    res_k1 = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=payload_k1,
    )
    assert res_k1.status_code == 200
    assert res_k1.json()["decision"] == "deny"
    assert res_k1.json()["reason"] == "stale_authority_epoch"
    assert res_k1.json()["consequence"]["target_invoked"] is False
    assert not (tmp_path / "handover-record.json").exists()

    # Incarnation 2 (K2) executes with its successor credentials and epoch 2 -> APPLIED
    payload_k2 = execute_payload(mount, operation_id="op_k2_fresh", resource="handover-record")
    payload_k2["token_id"] = h_json["token_id"]
    payload_k2["nonce"] = h_json["nonce"]
    payload_k2["authority_epoch"] = 2
    res_k2 = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=payload_k2,
    )
    assert res_k2.status_code == 200
    assert res_k2.json()["decision"] == "allow"
    assert res_k2.json()["consequence"]["target_invoked"] is True
    assert (tmp_path / "handover-record.json").exists()


def test_advance_epoch_refuses_omitted_authority_epoch(client, tmp_path: Path) -> None:
    """Proves Auditor Gap 1 closure:
    Once a mount's authority_epoch is advanced past 1 via /advance-epoch,
    a stale holder cannot bypass epoch fencing by omitting authority_epoch (sending None).
    """
    mount, _adapter = prepare(client, tmp_path)
    mount_id = mount["mount"]["id"]

    adv = client.post(f"/v1/capability/mounts/{mount_id}/advance-epoch")
    assert adv.status_code == 200, adv.text
    assert adv.json()["authority_epoch"] == 2

    # Stale holder omits authority_epoch altogether
    payload_omit = execute_payload(mount, operation_id="op_omit_epoch", resource="omit-epoch-record")
    payload_omit.pop("authority_epoch", None)
    res_omit = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=payload_omit,
    )
    assert res_omit.status_code == 200
    assert res_omit.json()["decision"] == "deny"
    assert res_omit.json()["reason"] == "stale_authority_epoch"
    assert res_omit.json()["consequence"]["target_invoked"] is False
    assert not (tmp_path / "omit-epoch-record.json").exists()

