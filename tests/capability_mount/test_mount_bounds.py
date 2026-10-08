"""A mount is bounded to the resources it names and to its package's own target.

Before this, ``execution_scope.resources`` was accepted and stored but never enforced
(authority for one record was authority for every record of that action), and a
mount could be executed against any registered target that happened to map the
action. A contract that says "modify record 42 on the protected dataset" must mount
exactly that, and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import LocalRecordAdapter, TargetAdapterRegistry
from cappo_backend.capability_mount.envelope import operation_envelope_digest
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.security.biscuit import extract_allowed_envelopes, extract_authority_context
from cappo_backend.security.evidence import get_evidence_key_pair, verify_signed_execution_evidence
from tests.capability_mount.test_terminate_fence import ConfirmedAnchor, execute_payload, records_package


class OtherTarget(LocalRecordAdapter):
    """A second, legitimately registered target that maps the same action."""

    ref = "unit.other-target"


def setup(client: TestClient, root: Path, *, target_ref: str | None = None) -> tuple[LocalRecordAdapter, OtherTarget]:
    registry = client.app.state.mount_registry
    package = records_package()
    if target_ref is not None:
        package = package.model_copy(update={"target_ref": target_ref})
    registry.register_package(package)
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    primary, other = LocalRecordAdapter(root / "primary"), OtherTarget(root / "other")
    registry.target_adapters.register(LocalRecordAdapter.ref, primary)
    registry.target_adapters.register(OtherTarget.ref, other)
    client.headers["X-Workspace-ID"] = "w1"
    return primary, other


def mount(client: TestClient, resources: list[str] | None = None) -> dict:
    scope: dict[str, object] = {"workspace": "w1", "project": "p1"}
    if resources is not None:
        scope["resources"] = resources
    return client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": scope,
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    ).json()


def execute(client: TestClient, body: dict, *, resource: str = "fenced-1", target_ref: str | None = None) -> dict:
    payload = execute_payload(body, f"op-{uuid4().hex}")
    payload["resource"] = resource
    if target_ref is not None:
        payload["target_ref"] = target_ref
    return client.post(f"/v1/capability/mounts/{body['mount']['id']}/execute", json=payload).json()


# --- resource bounds -----------------------------------------------------------------


def test_named_resource_is_granted_and_executes(client: TestClient, tmp_path: Path) -> None:
    primary, _ = setup(client, tmp_path)
    body = mount(client, ["fenced-1"])
    assert body["decision"] == "allow"
    assert body["mount"]["grants"]["resources"] == ["fenced-1"]

    result = execute(client, body, resource="fenced-1")

    assert result["decision"] == "allow" and result["consequence"]["state"] == "succeeded"
    assert primary.invocation_count == 1


def test_resource_outside_the_mount_is_denied_and_never_reaches_the_target(client: TestClient, tmp_path: Path) -> None:
    primary, _ = setup(client, tmp_path)
    body = mount(client, ["fenced-1"])

    result = execute(client, body, resource="fenced-2")

    assert (result["decision"], result["reason"]) == ("deny", "resource_not_granted")
    assert result["consequence"]["target_invoked"] is False
    assert primary.invocation_count == 0
    assert not (tmp_path / "primary" / "fenced-2.json").exists()
    # An out-of-bounds attempt is a policy violation: like a blocked action it ends the mount.
    assert result["consequence"]["terminated"] is True
    assert execute(client, body, resource="fenced-1")["decision"] == "deny"
    assert primary.invocation_count == 0


def test_evaluate_refuses_a_resource_outside_the_mount(client: TestClient, tmp_path: Path) -> None:
    setup(client, tmp_path)
    body = mount(client, ["fenced-1"])
    token = body["token"]
    evaluate = lambda resource: client.post(  # noqa: E731
        f"/v1/capability/mounts/{body['mount']['id']}/actions",
        json={"token_id": token["token_id"], "nonce": token["nonce"], "action": "record.create", "resource": resource},
    ).json()

    assert (evaluate("fenced-2")["decision"], evaluate("fenced-2")["reason"]) == ("deny", "resource_not_granted")
    assert evaluate("fenced-1")["decision"] == "allow"


def test_empty_resource_list_is_refused_rather_than_read_as_unbounded(client: TestClient, tmp_path: Path) -> None:
    setup(client, tmp_path)

    body = mount(client, [])

    assert body["decision"] == "deny"
    assert body.get("token") is None


def test_mount_without_resources_stays_unbounded(client: TestClient, tmp_path: Path) -> None:
    primary, _ = setup(client, tmp_path)
    body = mount(client)
    assert body["mount"]["grants"]["resources"] == []

    assert execute(client, body, resource="any-record")["consequence"]["state"] == "succeeded"
    assert primary.invocation_count == 1


# --- package -> target binding ---------------------------------------------------------


def test_package_target_is_published_by_discovery(client: TestClient, tmp_path: Path) -> None:
    setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)

    packages = {p["id"]: p for p in client.get("/v1/capability/packages").json()}

    assert packages["records@v1"]["target_ref"] == LocalRecordAdapter.ref


def test_mount_cannot_act_on_another_registered_target(client: TestClient, tmp_path: Path) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    body = mount(client, ["fenced-1"])

    refused = execute(client, body, target_ref=OtherTarget.ref)

    assert (refused["decision"], refused["reason"]) == ("deny", "target_not_bound_to_package")
    assert other.invocation_count == 0 and primary.invocation_count == 0
    # A preflight refusal neither consumes the nonce nor ends the mount: the bound op still runs.
    assert refused["consequence"]["terminated"] is False
    assert execute(client, body)["consequence"]["state"] == "succeeded"
    assert primary.invocation_count == 1 and other.invocation_count == 0


def test_package_without_a_target_keeps_registry_resolution(client: TestClient, tmp_path: Path) -> None:
    _, other = setup(client, tmp_path)
    body = mount(client)

    assert execute(client, body, target_ref=OtherTarget.ref)["consequence"]["state"] == "succeeded"
    assert other.invocation_count == 1


# --- the bound operation: one action, one resource, one target, exact arguments ----------
# Owner's negative controls (2026-10-07). Each denial happens before dispatch and leaves
# the sink untouched; the receipt names the actual bounded resource.

BOUND_ARGS = {"status": "approved"}


def bound_operation(**overrides: object) -> dict:
    return {"target_ref": LocalRecordAdapter.ref, "action": "record.create", "resource": "fenced-1",
            "arguments": dict(BOUND_ARGS), **overrides}


def mount_bound(client: TestClient, operation: dict | None = None, resources: list[str] | None = None) -> dict:
    scope: dict[str, object] = {"workspace": "w1", "project": "p1", "operation": operation or bound_operation()}
    if resources is not None:
        scope["resources"] = resources
    return client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": scope,
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    ).json()


def execute_bound(client: TestClient, body: dict, **overrides: object) -> dict:
    payload = execute_payload(body, f"op-{uuid4().hex}")
    payload.update({"resource": "fenced-1", "arguments": dict(BOUND_ARGS)})
    payload.update(overrides)
    return client.post(f"/v1/capability/mounts/{body['mount']['id']}/execute", json=payload).json()


def assert_untouched(primary: LocalRecordAdapter, other: LocalRecordAdapter, tmp_path: Path) -> None:
    assert primary.invocation_count == 0 and other.invocation_count == 0
    for root in (tmp_path / "primary", tmp_path / "other"):
        assert not root.exists() or not any(root.glob("*.json"))


def test_bound_mount_carries_its_bounds_into_the_signed_token(client: TestClient, db: Session, tmp_path: Path) -> None:
    setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    body = mount_bound(client)
    assert body["decision"] == "allow", body
    grants = body["mount"]["grants"]
    digest = operation_envelope_digest(package_ref="records@v1", target_ref=LocalRecordAdapter.ref,
                                       action="record.create", resource="fenced-1", arguments=BOUND_ARGS)
    assert (grants["writes"], grants["resources"], grants["envelope_digest"]) == (["record.create"], ["fenced-1"], digest)

    db.commit()
    row = db.execute(select(CapabilityMount).where(CapabilityMount.mount_id == body["mount"]["id"])).scalar_one()
    biscuit_token = row.token_json.get("biscuit_token")
    assert biscuit_token, "a bound mount must carry a Biscuit"
    authority = extract_authority_context(biscuit_token)
    assert authority is not None and authority.allowed_resources == {"fenced-1"}
    assert extract_allowed_envelopes(biscuit_token) == {digest}


def test_control_1_exact_operation_is_eligible_and_receipt_names_the_resource(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    primary, _ = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    body = mount_bound(client)

    result = execute_bound(client, body)

    assert result["decision"] == "allow" and result["consequence"]["state"] == "succeeded", result
    assert primary.invocation_count == 1
    db.commit()
    receipts = db.execute(
        select(CapabilityActionReceipt).where(CapabilityActionReceipt.mount_id == body["mount"]["id"])
    ).scalars().all()
    assert receipts and {r.resource for r in receipts} == {"fenced-1"}  # control 6: never "*"
    # The signed evidence names the authority actually exercised.
    public_key = get_evidence_key_pair().public_key()
    signed = [verify_signed_execution_evidence(r.signed_receipt_cose, public_key) for r in receipts]
    digest = body["mount"]["grants"]["envelope_digest"]
    assert all(
        (s["resource"], s["target_ref"], s["envelope_digest"]) == ("fenced-1", LocalRecordAdapter.ref, digest)
        for s in signed
    )


def test_envelope_digest_is_canonical_and_covers_every_dimension() -> None:
    base = {"package_ref": "arena.dataset@v1", "target_ref": "arena.protected-dataset", "action": "record.modify",
            "resource": "42", "arguments": {"note": "approved-by-veklom", "value": 39}}
    digest = operation_envelope_digest(**base)
    reordered = {**base, "arguments": {"value": 39, "note": "approved-by-veklom"}}
    assert operation_envelope_digest(**reordered) == digest  # key order is not meaning
    for change in (
        {"package_ref": "other@v1"},
        {"target_ref": "arena.other"},
        {"action": "record.delete"},
        {"resource": "43"},
        {"resource": "420"},
        {"arguments": {"note": "something-else", "value": 39}},
        {"arguments": {"note": "approved-by-veklom"}},
        {"arguments": {"note": "approved-by-veklom", "value": 39, "extra": None}},
        {"arguments": {"note": "approved-by-veklom", "value": "39"}},
    ):
        assert operation_envelope_digest(**{**base, **change}) != digest, change


def test_control_2_other_record_is_denied_before_dispatch(client: TestClient, tmp_path: Path) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    # A resource refusal ends the mount (policy violation), so each probe gets a fresh mount.
    for resource in ("fenced-2", "fenced-10", "fenced-1x", "fenced-1/child"):  # other record, prefix extensions, child
        result = execute_bound(client, mount_bound(client), resource=resource)
        assert result["decision"] == "deny", resource
        assert result["consequence"]["target_invoked"] is False
    assert_untouched(primary, other, tmp_path)  # control 7


def test_control_3_wrong_action_is_denied(client: TestClient, tmp_path: Path) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)

    for action in ("record.delete", "record.read"):
        result = execute_bound(client, mount_bound(client), action=action)
        assert result["decision"] == "deny", action
    assert_untouched(primary, other, tmp_path)


def test_control_4_wrong_target_is_denied(client: TestClient, tmp_path: Path) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)

    result = execute_bound(client, mount_bound(client), target_ref=OtherTarget.ref)

    assert (result["decision"], result["reason"]) == ("deny", "target_not_bound_to_package")
    assert_untouched(primary, other, tmp_path)


def test_control_5_altered_arguments_are_denied(client: TestClient, tmp_path: Path) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    body = mount_bound(client)

    for arguments in ({"status": "revoked"}, {"status": "approved", "extra": "x"}, {}):
        result = execute_bound(client, body, arguments=arguments)
        assert (result["decision"], result["reason"]) == ("deny", "operation_envelope_mismatch"), arguments
    assert_untouched(primary, other, tmp_path)
    # Preflight refusals leave the bound operation itself executable.
    assert execute_bound(client, body)["consequence"]["state"] == "succeeded"


def test_envelope_is_enforced_from_the_signed_token_not_from_stored_metadata(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    body = mount_bound(client)
    altered = {"status": "revoked"}
    forged = operation_envelope_digest(package_ref="records@v1", target_ref=LocalRecordAdapter.ref,
                                       action="record.create", resource="fenced-1", arguments=altered)
    # Someone with database write access rewrites the stored grant to the altered operation.
    db.commit()
    row = db.execute(select(CapabilityMount).where(CapabilityMount.mount_id == body["mount"]["id"])).scalar_one()
    token_json = dict(row.token_json)
    token_json["grants"] = {**token_json["grants"], "envelope_digest": forged}
    row.token_json = token_json
    db.commit()

    result = execute_bound(client, body, arguments=altered)

    assert (result["decision"], result["reason"]) == ("deny", "operation_envelope_unverifiable")
    assert_untouched(primary, other, tmp_path)


def _mint_dropping(monkeypatch: pytest.MonkeyPatch, *drop: str) -> None:
    """Simulate a minting bug that forgets to put a bound into the signed token."""
    import cappo_backend.security.biscuit as biscuit

    real = biscuit.mint_biscuit_capability

    def forgetful(*args: object, **kwargs: object) -> str:
        for key in drop:
            kwargs[key] = None
        return real(*args, **kwargs)

    monkeypatch.setattr(biscuit, "mint_biscuit_capability", forgetful)


def test_resource_bound_missing_from_the_token_fails_closed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    _mint_dropping(monkeypatch, "resources")
    body = mount(client, ["fenced-1"])  # grants say fenced-1; the Biscuit carries no resource fact

    result = execute(client, body, resource="fenced-1")

    assert (result["decision"], result["reason"]) == ("deny", "resource_bound_not_signed")
    assert_untouched(primary, other, tmp_path)


def test_envelope_missing_from_the_token_fails_closed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary, other = setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)
    _mint_dropping(monkeypatch, "envelope_digest")
    body = mount_bound(client)

    result = execute_bound(client, body)

    assert (result["decision"], result["reason"]) == ("deny", "operation_envelope_unverifiable")
    assert_untouched(primary, other, tmp_path)


def test_historical_v1_receipts_keep_their_canonical_form() -> None:
    from datetime import datetime, timezone

    from cappo_backend.models.capability_action_receipt import receipt_canonical
    from cappo_backend.services.canonical import sha256_json

    fields = dict(receipt_id="r", execution_id="e", mount_id="m", token_id="t", principal="p", action="a",
                  resource="42", target_ref="x", envelope_digest="d" * 64, decision="allow", reason="allowed",
                  actioned_at=datetime(2026, 1, 1, tzinfo=timezone.utc), content_hash="", policy_version="1.0")
    v1 = receipt_canonical(CapabilityActionReceipt(**fields))
    # Exactly the pre-2026-10-07 form: placeholder resource, no target/envelope/version keys.
    assert v1["resource"] == "*" and not {"target_ref", "envelope_digest", "receipt_schema_version"} & set(v1)
    v2 = receipt_canonical(CapabilityActionReceipt(**fields, receipt_schema_version=2))
    assert (v2["resource"], v2["target_ref"], v2["envelope_digest"], v2["receipt_schema_version"]) == (
        "42", "x", "d" * 64, 2)
    assert sha256_json(v1) != sha256_json(v2)
    with pytest.raises(ValueError):
        receipt_canonical(CapabilityActionReceipt(**fields, receipt_schema_version=3))


def test_mount_refuses_an_operation_its_package_cannot_bind(client: TestClient, tmp_path: Path) -> None:
    setup(client, tmp_path, target_ref=LocalRecordAdapter.ref)

    assert mount_bound(client, bound_operation(target_ref=OtherTarget.ref))["decision"] == "deny"
    assert mount_bound(client, bound_operation(action="record.delete"))["decision"] == "deny"
    assert mount_bound(client, resources=["fenced-2"])["decision"] == "deny"
    assert mount_bound(client, bound_operation(resource='x") or true; //'))["decision"] == "deny"


def test_package_without_target_cannot_be_operation_bound(client: TestClient, tmp_path: Path) -> None:
    setup(client, tmp_path)  # records@v1 publishes no target here

    assert mount_bound(client)["decision"] == "deny"
