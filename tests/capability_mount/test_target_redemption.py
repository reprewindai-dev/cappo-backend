"""Target-side authority check ("Model B"): a target redeems a permit before committing.

Before this, authority was only checked inside CAPPO: a target accepted anything that
carried the gateway's static credential, so a permit captured before revocation, or the
credential alone, was enough to write. Now the target asks CAPPO at the moment of commit.

Contract under test (decided under the mount row lock that terminate() takes):
- ALLOW only for CAPPO's own STARTED consequence on a live mount, with a permit matching
  the exact payload AND the target it was dispatched to, redeemed by that target
  (Ed25519 signature verified against the key pinned in CAPPO's configuration), once;
- DENY after terminate, for a different payload, at a different target, for a target
  that only claims an identity, on replay, for unknown operations, and when permits are
  not configured.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import (
    ConsequenceContext,
    LocalRecordAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.errors import TargetRefusedError
from cappo_backend.capability_mount.models import Decision
from cappo_backend.capability_mount.permits import redeem_message
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.models.consequence_redemption import ConsequenceRedemption
from tests.capability_mount.test_terminate_fence import (
    ConfirmedAnchor,
    execute_payload,
    op_states,
    owner_terminate,
    records_package,
)

PERMIT_KEY = "unit-test-permit-key"
TARGET_REF = LocalRecordAdapter.ref  # the ref CAPPO dispatches to, from its own registry


def new_key() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private, public_hex


def signed_redeem(registry: MountRegistry, private: Ed25519PrivateKey, sink_ref: str, operation_id: str,
                  permit: str, digest: str) -> tuple[Decision, str]:
    message = redeem_message(operation_id=operation_id, payload_sha256=digest, permit=permit, target_ref=sink_ref)
    return registry.redeem_consequence(
        operation_id, permit, digest, sink_ref=sink_ref, target_signature=private.sign(message).hex()
    )


class RedeemingTarget(LocalRecordAdapter):
    """A target that asks CAPPO before it writes, as an external sink would over HTTP.

    It holds its own Ed25519 key; CAPPO pins the public half (redeem_public_key_hex).
    ``present_as`` / ``sign_with`` let a test make it redeem under another identity.
    """

    def __init__(self, root: Path, db: Session, settings: object, *, before_redeem=None, tamper=False,
                 twice=False, present_as: str | None = None, sign_with: Ed25519PrivateKey | None = None):
        super().__init__(root)
        self.db = db
        self.settings = settings
        self.before_redeem = before_redeem
        self.tamper = tamper
        self.twice = twice
        self.present_as = present_as
        self.private_key, self.redeem_public_key_hex = new_key()
        self.sign_with = sign_with
        self.adapters: TargetAdapterRegistry | None = None
        self.decisions: list[tuple[Decision, str]] = []
        self.seen: dict[str, str] = {}

    def _redeem(self, operation_id: str, permit: str, digest: str) -> tuple[Decision, str]:
        with Session(bind=self.db.get_bind(), autoflush=False, expire_on_commit=False) as other:
            registry = MountRegistry(db=other, anchor=ConfirmedAnchor(), settings=self.settings,
                                     target_adapters=self.adapters)
            return signed_redeem(registry, self.sign_with or self.private_key, self.present_as or TARGET_REF,
                                 operation_id, permit, digest)

    def dispatch(self, context: ConsequenceContext) -> object:
        assert context.permit_for is not None, "CAPPO must hand the target a permit"
        payload = json.dumps(dict(context.arguments), sort_keys=True).encode()
        permit = context.permit_for(payload)
        digest = hashlib.sha256(payload).hexdigest()
        self.seen = {"operation_id": context.operation_id, "permit": permit, "digest": digest}
        if self.before_redeem:
            self.before_redeem()
        if self.tamper:
            digest = hashlib.sha256(payload + b"tampered").hexdigest()
        result = self._redeem(context.operation_id, permit, digest)
        self.decisions.append(result)
        if self.twice:
            self.decisions.append(self._redeem(context.operation_id, permit, digest))
        if result[0] is not Decision.ALLOW:
            raise TargetRefusedError(f"target_refused:{result[1]}")
        return super().dispatch(context)


class OtherTarget(LocalRecordAdapter):
    """A second, legitimately registered target with its own pinned key."""

    ref = "unit.other-target"

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.private_key, self.redeem_public_key_hex = new_key()


def prepare(client: TestClient, adapter: LocalRecordAdapter, *, key: str = PERMIT_KEY,
            extra: tuple[LocalRecordAdapter, ...] = ()) -> dict:
    client.app.state.settings.consequence_permit_key = key
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(TARGET_REF, adapter)
    for other in extra:
        registry.target_adapters.register(other.ref, other)
    if isinstance(adapter, RedeemingTarget):
        adapter.adapters = registry.target_adapters
    client.headers["X-Workspace-ID"] = "w1"
    body = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    ).json()
    assert body["decision"] == "allow"
    return body


def run(client: TestClient, mount: dict, operation_id: str) -> dict:
    payload = execute_payload(mount, operation_id)
    payload["arguments"] = {"status": "active", "mount_id": mount["mount"]["id"]}
    return client.post(f"/v1/capability/mounts/{mount['mount']['id']}/execute", json=payload).json()


# --- the five decisive cases -------------------------------------------------------


def test_correct_target_and_payload_redeems_once_and_commits(client: TestClient, db: Session, tmp_path: Path) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings, twice=True)
    mount = prepare(client, target)
    operation_id = f"op-{uuid4().hex}"

    body = run(client, mount, operation_id)

    assert target.decisions[0] == (Decision.ALLOW, "redeemed")
    assert target.decisions[1] == (Decision.DENY, "already_redeemed")  # replay while still in flight
    assert body["consequence"]["state"] == "succeeded"
    assert (tmp_path / "fenced-1.json").exists()
    db.commit()
    row = db.execute(select(ConsequenceRedemption).where(ConsequenceRedemption.operation_id == operation_id)).scalar_one()
    assert row.payload_sha256 == target.seen["digest"] and row.sink_ref == TARGET_REF
    # After the consequence settled the mount is terminated: the same permit is dead.
    assert target._redeem(operation_id, target.seen["permit"], target.seen["digest"]) == (Decision.DENY, "mount_terminated")


def test_wrong_target_cannot_redeem_a_valid_permit_for_another_target(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    """Target B is registered and authenticated, but the permit was minted for target A."""
    other = OtherTarget(tmp_path / "other")
    target = RedeemingTarget(tmp_path, db, client.app.state.settings,
                             present_as=OtherTarget.ref, sign_with=other.private_key)
    mount = prepare(client, target, extra=(other,))

    body = run(client, mount, f"op-{uuid4().hex}")

    assert target.decisions == [(Decision.DENY, "permit_mismatch")]
    assert not (tmp_path / "fenced-1.json").exists()
    assert body["consequence"]["state"] == "failed"
    db.commit()
    assert db.execute(select(ConsequenceRedemption)).scalars().all() == []


def test_permit_is_bound_to_the_exact_payload(client: TestClient, db: Session, tmp_path: Path) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings, tamper=True)
    mount = prepare(client, target)

    body = run(client, mount, f"op-{uuid4().hex}")

    assert target.decisions == [(Decision.DENY, "permit_mismatch")]
    assert not (tmp_path / "fenced-1.json").exists()
    assert body["consequence"]["state"] == "failed"


def test_revocation_between_dispatch_and_commit_is_refused_at_the_target(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    """Post-admission, pre-reality revocation: CAPPO admitted and dispatched, then authority ended."""
    fence: list[tuple] = []
    target = RedeemingTarget(tmp_path, db, client.app.state.settings)
    mount = prepare(client, target)
    mount_id = mount["mount"]["id"]
    target.before_redeem = lambda: fence.append(owner_terminate(db, mount_id, settle_seconds=0.05))
    operation_id = f"op-{uuid4().hex}"

    body = run(client, mount, operation_id)

    decision, reason, _anchor, in_flight = fence[0]
    assert (decision, reason, in_flight) == (Decision.ALLOW, "terminated_in_flight", [operation_id])
    assert target.decisions == [(Decision.DENY, "mount_terminated")]
    assert not (tmp_path / "fenced-1.json").exists(), "the admitted consequence must not become reality"
    assert target.invocation_count == 0
    assert body["consequence"]["state"] == "failed"
    assert op_states(db, operation_id) == ["authorized", "started", "failed"]
    db.commit()
    assert db.execute(select(ConsequenceRedemption)).scalars().all() == []


def test_replay_after_redemption_is_refused(client: TestClient, db: Session, tmp_path: Path) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings)
    mount = prepare(client, target)
    operation_id = f"op-{uuid4().hex}"
    run(client, mount, operation_id)
    assert target.decisions == [(Decision.ALLOW, "redeemed")]

    # Same target, same permit, same payload, presented again later.
    replay = target._redeem(operation_id, target.seen["permit"], target.seen["digest"])

    assert replay[0] is Decision.DENY
    assert replay[1] in {"already_redeemed", "mount_terminated"}
    db.commit()
    assert len(db.execute(select(ConsequenceRedemption)).scalars().all()) == 1


# --- identity: claiming to be the target is not enough -----------------------------


def test_a_target_claiming_another_identity_is_refused(client: TestClient, db: Session, tmp_path: Path) -> None:
    """Right name, wrong key: the impostor cannot produce the pinned target's signature."""
    impostor_key, _ = new_key()
    target = RedeemingTarget(tmp_path, db, client.app.state.settings, sign_with=impostor_key)
    mount = prepare(client, target)

    body = run(client, mount, f"op-{uuid4().hex}")

    assert target.decisions == [(Decision.DENY, "target_signature_invalid")]
    assert not (tmp_path / "fenced-1.json").exists()
    assert body["consequence"]["state"] == "failed"


def test_correct_target_key_but_wrong_target_ref_is_refused(client: TestClient, db: Session, tmp_path: Path) -> None:
    """The real target's key, presenting another registered target's ref: the signature
    is checked against the key pinned for the *claimed* ref, so it fails."""
    other = OtherTarget(tmp_path / "other")
    target = RedeemingTarget(tmp_path, db, client.app.state.settings, present_as=OtherTarget.ref)
    mount = prepare(client, target, extra=(other,))

    body = run(client, mount, f"op-{uuid4().hex}")

    assert target.decisions == [(Decision.DENY, "target_signature_invalid")]
    assert not (tmp_path / "fenced-1.json").exists()
    assert body["consequence"]["state"] == "failed"


def test_unsigned_redemption_is_refused_even_for_the_bound_target(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings)
    mount = prepare(client, target)
    seen: dict[str, str] = {}

    def capture_and_ask_unsigned() -> None:
        registry = MountRegistry(db=db, anchor=ConfirmedAnchor(), settings=client.app.state.settings,
                                 target_adapters=target.adapters)
        seen["unsigned"] = registry.redeem_consequence(
            target.seen["operation_id"], target.seen["permit"], target.seen["digest"], sink_ref=TARGET_REF
        )[1]

    target.before_redeem = capture_and_ask_unsigned
    run(client, mount, f"op-{uuid4().hex}")

    assert seen["unsigned"] == "target_signature_invalid"
    # The genuine, signed redemption that followed still went through exactly once.
    assert target.decisions == [(Decision.ALLOW, "redeemed")]


def test_redemption_evidence_records_the_authenticated_target(client: TestClient, db: Session, tmp_path: Path) -> None:
    """sink_ref is stored only after its signature verified, so evidence names who redeemed."""
    target = RedeemingTarget(tmp_path, db, client.app.state.settings)
    mount = prepare(client, target)
    operation_id = f"op-{uuid4().hex}"
    run(client, mount, operation_id)
    db.commit()
    rows = db.execute(select(ConsequenceRedemption)).scalars().all()
    assert [(r.operation_id, r.sink_ref) for r in rows] == [(operation_id, TARGET_REF)]


def test_unregistered_or_unpinned_targets_cannot_redeem(client: TestClient, db: Session, tmp_path: Path) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings)
    mount = prepare(client, target)
    run(client, mount, f"op-{uuid4().hex}")
    registry = MountRegistry(db=db, anchor=ConfirmedAnchor(), settings=client.app.state.settings,
                             target_adapters=target.adapters)
    key, _ = new_key()
    assert signed_redeem(registry, key, "nobody.registered", "x", "f" * 64, "0" * 64) == (
        Decision.DENY, "target_not_registered_for_redemption")
    assert registry.redeem_consequence("x", "f" * 64, "0" * 64) == (Decision.DENY, "target_identity_required")


def test_redeem_route_verifies_the_signature_header_then_refuses_what_cappo_did_not_dispatch(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    target = RedeemingTarget(tmp_path, db, client.app.state.settings)
    prepare(client, target)
    body = {"operation_id": "never-dispatched", "permit": "f" * 64, "payload_sha256": "0" * 64, "sink_ref": TARGET_REF}

    unsigned = client.post("/v1/capability/redeem", json=body)
    assert unsigned.json()["reason"] == "target_signature_invalid"

    message = redeem_message(operation_id="never-dispatched", payload_sha256="0" * 64, permit="f" * 64,
                             target_ref=TARGET_REF)
    signed = client.post("/v1/capability/redeem", json=body,
                         headers={"X-Veklom-Target-Signature": target.private_key.sign(message).hex()})
    assert signed.status_code == 200
    assert signed.json() == {"decision": "deny", "reason": "unknown_operation", "operation_id": "never-dispatched"}


def test_without_a_permit_key_nothing_can_be_redeemed(client: TestClient, db: Session, tmp_path: Path) -> None:
    seen: list[object] = []

    class Plain(LocalRecordAdapter):
        def dispatch(self, context: ConsequenceContext) -> object:
            seen.append(context.permit_for)
            return super().dispatch(context)

    mount = prepare(client, Plain(tmp_path), key="")
    client.app.state.settings.approval_token_signing_key = ""
    run(client, mount, f"op-{uuid4().hex}")
    assert seen == [None]
    registry = MountRegistry(db=db, anchor=ConfirmedAnchor(), settings=client.app.state.settings)
    assert registry.redeem_consequence("anything", "x", "0" * 64) == (Decision.DENY, "permits_not_configured")
