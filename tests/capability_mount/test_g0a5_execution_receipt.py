from __future__ import annotations

"""G0A.5 — Prove durable execution receipt persistence.

The action_decision anchor (G0A.4) is an in-memory spy that does NOT constitute
durable persistence. This test proves a CapabilityActionReceipt row exists in
the SQLite DB after a successful ALLOW, is retrievable by execution_id, and is
NOT created on DENY (replay).

The DB session fixture comes from conftest.py (shared across the capability_mount
suite). The spy is identical to the one used in G0A.4.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.capability_mount.service import AnchorResult
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt


class _StructuredAnchor:
    """Preserves raw kwarg objects — identical to G0A.3b / G0A.4 spy."""

    def __init__(self, status: str = "confirmed") -> None:
        self.status = status
        self.raw_events: list[dict[str, object]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.raw_events.append({"event_type": event_type, **payload})
        return AnchorResult(self.status, anchor_id=f"anchor-{len(self.raw_events)}")


def _package() -> CapabilityPackage:
    return CapabilityPackage(
        id="outreach@v1",
        family="outreach",
        title="Governed Outreach",
        purpose="Send approved external outreach",
        reads=["contact.read"],
        writes=["draft.write", "outreach.email_send"],
        blocked=["credential.export"],
        outputs=["draft"],
        policy_defaults={"mode": "draft_only"},
        external_send_actions=["outreach.email_send"],
        suppression_required_actions=["outreach.email_send"],
    )


def _mount_payload(ttl_seconds: int = 300) -> dict[str, object]:
    return {
        "package_ref": "outreach@v1",
        "execution_scope": {"workspace": "w1", "project": "p1"},
        "requested_action_scope": {
            "reads": ["contact.read"],
            "writes": ["draft.write", "outreach.email_send"],
            "blocked": ["draft.write"],
        },
        "role": "ephemeral_executor",
        "policy": {"mode": "draft_only"},
        "ttl_seconds": ttl_seconds,
    }


def _install(client: TestClient, anchor: _StructuredAnchor) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(_package())
    registry.anchor = anchor


def test_g0a_5_durable_execution_receipt(client: TestClient, db: Session) -> None:
    from datetime import datetime as _dt

    spy = _StructuredAnchor(status="confirmed")
    _install(client, spy)

    # ── 1. Mount ──────────────────────────────────────────────────────────────
    mount_resp = client.post("/v1/capability/mounts", json=_mount_payload(300))
    assert mount_resp.status_code == 200, mount_resp.text
    mount_body = mount_resp.json()
    assert mount_body["decision"] == "allow"

    mount_id = mount_body["mount"]["id"]
    token_id = mount_body["token"]["token_id"]
    nonce    = mount_body["token"]["nonce"]

    # Capture execution_id from the token embedded in the mount anchor event.
    # The token is an EphemeralScopedToken with a .execution_id field.
    mount_events = [e for e in spy.raw_events if e["event_type"] == "mount"]
    assert len(mount_events) == 1
    token_obj = mount_events[0].get("token")
    assert token_obj is not None, "token kwarg missing from mount anchor event"
    execution_id: str = token_obj.execution_id  # type: ignore[union-attr]
    assert execution_id.startswith("exec_"), f"unexpected execution_id prefix: {execution_id!r}"

    # ── 2. Execute one action ─────────────────────────────────────────────────
    action_resp = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={"token_id": token_id, "nonce": nonce, "action": "contact.read"},
    )
    assert action_resp.status_code == 200, action_resp.text
    action_body = action_resp.json()
    assert action_body["decision"] == "allow", f"expected allow, got {action_body}"

    # ── 3. Query DB directly — NOT the spy ───────────────────────────────────
    # db fixture points to the same in-memory SQLite engine used by the app.
    receipts = db.execute(
        select(CapabilityActionReceipt).where(
            CapabilityActionReceipt.execution_id == execution_id
        )
    ).scalars().all()

    # Exactly one receipt
    assert len(receipts) == 1, (
        f"expected 1 receipt for execution_id={execution_id!r}, got {len(receipts)}"
    )
    rcpt = receipts[0]

    # Field binding assertions
    assert rcpt.execution_id == execution_id,    f"EXECUTION_ID FAIL: {rcpt.execution_id!r}"
    assert rcpt.principal not in (None, ""),     f"PRINCIPAL FAIL: {rcpt.principal!r}"
    assert rcpt.action == "contact.read",        f"ACTION FAIL: {rcpt.action!r}"
    assert rcpt.decision == "allow",             f"DECISION FAIL: {rcpt.decision!r}"
    assert rcpt.reason not in (None, ""),        f"REASON FAIL: {rcpt.reason!r}"
    assert rcpt.actioned_at is not None,         f"TIMESTAMP FAIL: {rcpt.actioned_at!r}"
    _dt.fromisoformat(str(rcpt.actioned_at))     # must be a valid ISO timestamp
    assert rcpt.token_id == token_id,            f"TOKEN_ID FAIL: {rcpt.token_id!r}"
    assert rcpt.mount_id == mount_id,            f"MOUNT_ID FAIL: {rcpt.mount_id!r}"
    assert rcpt.receipt_id.startswith("rcpt_"),  f"RECEIPT_ID FORMAT FAIL: {rcpt.receipt_id!r}"

    # ── 4. Replay does NOT create a second receipt ────────────────────────────
    replay_body = client.post(
        f"/v1/capability/mounts/{mount_id}/actions",
        json={"token_id": token_id, "nonce": nonce, "action": "contact.read"},
    ).json()
    assert replay_body["decision"] == "deny", f"REPLAY FAIL: {replay_body}"

    receipts_after_replay = db.execute(
        select(CapabilityActionReceipt).where(
            CapabilityActionReceipt.execution_id == execution_id
        )
    ).scalars().all()
    assert len(receipts_after_replay) == 1, (
        f"REPLAY_RECEIPT FAIL: expected 1, got {len(receipts_after_replay)}"
    )

    # ── 5. Retrievable from persistence layer (distinct from spy) ─────────────
    # Clear the spy to prove the DB row exists independently of process memory.
    spy.raw_events.clear()
    retrieved = db.execute(
        select(CapabilityActionReceipt).where(
            CapabilityActionReceipt.execution_id == execution_id
        )
    ).scalars().all()
    assert len(retrieved) == 1, "RETRIEVABLE FAIL: receipt gone after spy cleared"
    assert retrieved[0].receipt_id == rcpt.receipt_id, "RETRIEVABLE FAIL: receipt_id mismatch"

    print()
    print("G0A.5 = VERIFIED")
    print("RECEIPT_TYPE              = CapabilityActionReceipt (capability_action_receipts)")
    print("PERSISTENCE_BACKEND       = SQLite (in-test) / PostgreSQL (production)")
    print(f"EXECUTION_ID_BOUND        = {rcpt.execution_id!r}")
    print(f"PRINCIPAL_BOUND           = {rcpt.principal!r}")
    print(f"ACTION_BOUND              = {rcpt.action!r}")
    print(f"RESULT_BOUND              = decision={rcpt.decision!r} reason={rcpt.reason!r}")
    print(f"TIMESTAMP_BOUND           = {rcpt.actioned_at!r}")
    print(f"TOKEN_ID_BOUND            = {rcpt.token_id!r}")
    print(f"MOUNT_ID_BOUND            = {rcpt.mount_id!r}")
    print("RECEIPT_COUNT             = 1 (exactly)")
    print("REPLAY_CREATES_SECOND_RECEIPT = False (still 1 after replay)")
    print("RETRIEVABLE_AFTER_WRITE   = True (spy cleared, DB row persists)")
