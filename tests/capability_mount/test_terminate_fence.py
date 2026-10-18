"""Regression: terminate is a fence.

Lineage re-formation experiment, run lre-20261004T221739Z (auditor F10):
terminate returned "terminated" while a consequence that had already passed
evaluate() was still in flight, and its effect landed 3-15 ms later. evaluate()
commits and releases the mount row lock; AUTHORIZED, STARTED and the adapter
dispatch followed without re-checking ``terminated``.

Contract under test:
- a consequence that has not reached STARTED when the fence commits is refused
  (FAILED, mount_terminated) and never dispatched;
- a plain "terminated" / "already_terminated" means no consequence on the mount
  is authorized or started, so no further effect can land; otherwise terminate
  answers "terminated_in_flight" and names the operations.
"""

from __future__ import annotations

import os
import random
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from cappo_backend.capability_mount.effects import (
    ConsequenceContext,
    LocalRecordAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    MountPolicy,
    MountScope,
    UnmountReason,
)
from cappo_backend.capability_mount.service import AnchorResult, MountRegistry
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent


class ConfirmedAnchor:
    def anchor(self, event_type: str, **_: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id=f"fence-{event_type}-{uuid4().hex}")


def records_package(package_id: str = "records@v1") -> CapabilityPackage:
    return CapabilityPackage(
        id=package_id,
        family="activation",
        title="Activation Records",
        purpose="Manage activation records",
        reads=["record.read"],
        writes=["record.create"],
        outputs=["record"],
        policy_defaults={"mode": "record"},
    )


def prepare(client: TestClient, adapter: LocalRecordAdapter) -> dict[str, object]:
    registry = client.app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(LocalRecordAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"
    mounted = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    )
    assert mounted.status_code == 200
    body = mounted.json()
    assert body["decision"] == "allow"
    return body


def execute_payload(mount: dict[str, object], operation_id: str) -> dict[str, object]:
    token = mount["token"]
    return {
        "token_id": token["token_id"],
        "nonce": token["nonce"],
        "action": "record.create",
        "target_ref": LocalRecordAdapter.ref,
        "resource": "fenced-1",
        "arguments": {"status": "active"},
        "operation_id": operation_id,
    }


def owner_terminate(db: Session, mount_id: str, **kwargs: object):
    """Terminate as the owner from a separate session, as a concurrent request would."""
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as other:
        row = other.execute(
            select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        ).scalar_one()
        registry = MountRegistry(db=other, anchor=ConfirmedAnchor())
        return registry.terminate_with_in_flight(
            mount_id,
            UnmountReason.EXPLICIT_TERMINATE,
            owner_principal=row.owner_principal,
            owner_workspace=row.owner_workspace,
            **kwargs,
        )


def op_states(db: Session, operation_id: str) -> list[str]:
    db.commit()
    return [
        state
        for (state,) in db.execute(
            select(ConsequenceExecutionEvent.state)
            .where(ConsequenceExecutionEvent.operation_id == operation_id)
            .order_by(ConsequenceExecutionEvent.version)
        ).all()
    ]


def test_terminate_after_evaluate_commit_refuses_dispatch(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The F10 interleaving: the fence lands after evaluate() commits, before dispatch."""
    adapter = LocalRecordAdapter(tmp_path)
    mount = prepare(client, adapter)
    mount_id = mount["mount"]["id"]
    operation_id = f"fence-{uuid4().hex}"
    fence: list[tuple] = []

    original_evaluate = MountRegistry.evaluate

    def evaluate_then_fence(self, *args, **kwargs):
        result = original_evaluate(self, *args, **kwargs)
        if result[0] is Decision.ALLOW:
            fence.append(owner_terminate(db, mount_id))
        return result

    monkeypatch.setattr(MountRegistry, "evaluate", evaluate_then_fence)

    body = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json=execute_payload(mount, operation_id),
    ).json()

    # The fence answered a plain "terminated" with nothing in flight ...
    assert len(fence) == 1
    decision, reason, _anchor, in_flight = fence[0]
    assert (decision, reason, in_flight) == (Decision.ALLOW, "terminated", [])
    # ... so no effect may land afterwards.
    assert adapter.invocation_count == 0
    assert not (tmp_path / "fenced-1.json").exists()
    assert body["decision"] == "deny"
    assert body["reason"] == "mount_terminated"
    assert body["consequence"]["state"] == "failed"
    assert body["consequence"]["target_invoked"] is False
    assert op_states(db, operation_id) == ["authorized", "failed"]
    refusal = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == operation_id)
        .where(ConsequenceExecutionEvent.state == "failed")
    ).scalar_one()
    assert refusal.error_summary == "mount_terminated"
    assert refusal.completion_proof_type == "fence_refusal"


class FencingAdapter(LocalRecordAdapter):
    """Terminates the mount from inside dispatch, i.e. while the op is STARTED."""

    def __init__(self, root: Path, db: Session, settle_seconds: float) -> None:
        super().__init__(root)
        self.db = db
        self.settle_seconds = settle_seconds
        self.fence: list[tuple] = []

    def dispatch(self, context: ConsequenceContext) -> object:
        mount_id = context.arguments["mount_id"]
        self.fence.append(owner_terminate(self.db, mount_id, settle_seconds=self.settle_seconds))
        return super().dispatch(context)


def test_terminate_during_started_reports_in_flight(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    """A STARTED op may still land, so terminate must not answer a plain "terminated"."""
    adapter = FencingAdapter(tmp_path, db, settle_seconds=0.05)
    mount = prepare(client, adapter)
    mount_id = mount["mount"]["id"]
    operation_id = f"fence-{uuid4().hex}"
    payload = execute_payload(mount, operation_id)
    payload["arguments"] = {"status": "active", "mount_id": mount_id}

    body = client.post(f"/v1/capability/mounts/{mount_id}/execute", json=payload).json()

    decision, reason, _anchor, in_flight = adapter.fence[0]
    assert (decision, reason, in_flight) == (Decision.ALLOW, "terminated_in_flight", [operation_id])
    # The started op completes; that effect was reported in flight, not hidden.
    assert body["consequence"]["state"] == "succeeded"
    assert adapter.invocation_count == 1

    # Once it has settled, a repeat terminate is a plain fence again.
    again = client.post(
        f"/v1/capability/mounts/{mount_id}/terminate", json={"reason": "explicit_terminate"}
    ).json()
    assert again["reason"] == "already_terminated"
    assert again["in_flight_operation_ids"] == []


# --------------------------------------------------------------------------
# PostgreSQL: real row locks and real concurrency.
# --------------------------------------------------------------------------


def _postgres_factory():
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("fence concurrency test requires PostgreSQL DATABASE_URL")
    engine = create_engine(url, pool_pre_ping=True, pool_size=10)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class TimedAdapter:
    """Records when each effect lands; optionally holds dispatch open."""

    ref = "fence.timed"
    actions = frozenset({"record.create"})

    def __init__(self, hold: threading.Event | None = None, delay: float = 0.0) -> None:
        self.hold = hold
        self.delay = delay
        self.entered = threading.Event()
        self.effects: dict[str, int] = {}
        self.lock = threading.Lock()

    def dispatch(self, context: ConsequenceContext) -> object:
        self.entered.set()
        if self.hold is not None:
            assert self.hold.wait(timeout=10)
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.effects[context.operation_id] = time.monotonic_ns()
        return {"ok": True}

    def read_state(self, context: ConsequenceContext) -> object:
        raise KeyError(context.resource)


PG_OWNER = "fence:owner"
PG_WORKSPACE = "fence-workspace"


def _pg_registry(session: Session, package: CapabilityPackage, adapter: TimedAdapter) -> MountRegistry:
    registry = MountRegistry(db=session, anchor=ConfirmedAnchor())
    registry.register_package(package)
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(TimedAdapter.ref, adapter)
    return registry


def _pg_mount(factory, package: CapabilityPackage, adapter: TimedAdapter) -> tuple[str, str, str]:
    with factory() as session:
        record, anchor, reason, _holder = _pg_registry(session, package, adapter).request_mount(
            package.id,
            MountScope(workspace=PG_WORKSPACE, project="fence-project"),
            role="ephemeral_executor",
            policy=MountPolicy(),
            ttl_seconds=300,
            owner_principal=PG_OWNER,
            owner_workspace=PG_WORKSPACE,
        )
        assert reason == "mounted" and record is not None
        return record.mount.id, record.token.token_id, record.token.nonce


def _pg_execute(factory, package, adapter, mount, operation_id, out: dict) -> None:
    mount_id, token_id, nonce = mount
    try:
        with factory() as session:
            out["execute"] = _pg_registry(session, package, adapter).execute_consequence(
                mount_id,
                "record.create",
                token_id=token_id,
                nonce=nonce,
                target_ref=TimedAdapter.ref,
                resource="fenced-1",
                arguments={},
                operation_id=operation_id,
                owner_principal=PG_OWNER,
                owner_workspace=PG_WORKSPACE,
            )
    except BaseException as exc:  # pragma: no cover - surfaced by assertions
        out["error"] = exc


def _pg_terminate(factory, package, adapter, mount_id, out: dict, **kwargs) -> None:
    try:
        with factory() as session:
            result = _pg_registry(session, package, adapter).terminate_with_in_flight(
                mount_id,
                UnmountReason.EXPLICIT_TERMINATE,
                owner_principal=PG_OWNER,
                owner_workspace=PG_WORKSPACE,
                **kwargs,

            )
            out["fence_return_ns"] = time.monotonic_ns()
            out["fence"] = result
    except BaseException as exc:  # pragma: no cover - surfaced by assertions
        out["error"] = exc


def _pg_latest_state(factory, operation_id: str) -> str | None:
    with factory() as session:
        return session.execute(
            select(ConsequenceExecutionEvent.state)
            .where(ConsequenceExecutionEvent.operation_id == operation_id)
            .order_by(ConsequenceExecutionEvent.version.desc())
            .limit(1)
        ).scalar_one_or_none()


def test_postgres_terminate_waits_for_started_effect() -> None:
    factory = _postgres_factory()
    package = records_package(f"fence-{uuid4().hex[:10]}@v1")
    hold = threading.Event()
    adapter = TimedAdapter(hold=hold)
    mount = _pg_mount(factory, package, adapter)
    operation_id = f"fence-{uuid4().hex}"
    executed: dict = {}
    fenced: dict = {}

    executor = threading.Thread(
        target=_pg_execute, args=(factory, package, adapter, mount, operation_id, executed)
    )
    executor.start()
    assert adapter.entered.wait(timeout=10)  # op is STARTED and dispatching
    fencer = threading.Thread(
        target=_pg_terminate, args=(factory, package, adapter, mount[0], fenced),
        kwargs={"settle_seconds": 5},
    )
    fencer.start()
    time.sleep(0.2)
    assert fencer.is_alive(), "terminate answered while a started effect was still in flight"
    hold.set()
    executor.join(timeout=20)
    fencer.join(timeout=20)

    assert "error" not in executed and "error" not in fenced
    decision, reason, _anchor, in_flight = fenced["fence"]
    assert (decision, reason, in_flight) == (Decision.ALLOW, "terminated", [])
    assert adapter.effects[operation_id] < fenced["fence_return_ns"]
    assert _pg_latest_state(factory, operation_id) == "succeeded"


def test_postgres_terminate_reports_started_effect_it_did_not_wait_for() -> None:
    factory = _postgres_factory()
    package = records_package(f"fence-{uuid4().hex[:10]}@v1")
    hold = threading.Event()
    adapter = TimedAdapter(hold=hold)
    mount = _pg_mount(factory, package, adapter)
    operation_id = f"fence-{uuid4().hex}"
    executed: dict = {}
    fenced: dict = {}

    executor = threading.Thread(
        target=_pg_execute, args=(factory, package, adapter, mount, operation_id, executed)
    )
    executor.start()
    assert adapter.entered.wait(timeout=10)
    _pg_terminate(factory, package, adapter, mount[0], fenced, settle_seconds=0.1)
    hold.set()
    executor.join(timeout=20)

    assert "error" not in executed and "error" not in fenced
    decision, reason, _anchor, in_flight = fenced["fence"]
    assert (decision, reason, in_flight) == (Decision.ALLOW, "terminated_in_flight", [operation_id])
    assert adapter.effects[operation_id] > fenced["fence_return_ns"]


def test_postgres_fence_race_no_effect_after_plain_terminated() -> None:
    """Concurrent execute and terminate with random offsets, as in the experiment."""
    factory = _postgres_factory()
    package = records_package(f"fence-{uuid4().hex[:10]}@v1")
    adapter = TimedAdapter(delay=0.005)
    rng = random.Random(42)
    outcomes: list[str] = []

    for _ in range(40):
        mount = _pg_mount(factory, package, adapter)
        operation_id = f"fence-{uuid4().hex}"
        executed: dict = {}
        fenced: dict = {}
        offset = rng.uniform(0, 0.25)

        def delayed_terminate() -> None:
            time.sleep(offset)
            _pg_terminate(factory, package, adapter, mount[0], fenced)

        threads = [
            threading.Thread(
                target=_pg_execute, args=(factory, package, adapter, mount, operation_id, executed)
            ),
            threading.Thread(target=delayed_terminate),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert "error" not in executed and "error" not in fenced
        _decision, reason, _anchor, in_flight = fenced["fence"]
        landed = adapter.effects.get(operation_id)
        if reason in ("terminated", "already_terminated"):
            assert in_flight == []
            assert landed is None or landed < fenced["fence_return_ns"], (
                f"effect landed {(landed - fenced['fence_return_ns']) / 1e6:.3f} ms after "
                f"terminate answered {reason!r}"
            )
        else:
            assert reason == "terminated_in_flight" and in_flight == [operation_id]
        assert _pg_latest_state(factory, operation_id) not in ("authorized", "started")
        outcomes.append(executed["execute"][1])

    print("fence race outcomes:", {o: outcomes.count(o) for o in set(outcomes)})

    # Both sides of the race were exercised.
    assert "allowed" in outcomes
    assert any(outcome != "allowed" for outcome in outcomes)
