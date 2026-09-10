from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import cappo_backend.models  # noqa: F401
from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    MountPolicy,
    MountScope,
)
from cappo_backend.capability_mount.service import (
    AnchorResult,
    MountRegistry,
    _consume_nonce_atomically,
)
from cappo_backend.db.base import Base
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.capability_mount import CapabilityMount


class _ConfirmedAnchor:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.events: list[dict[str, object]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.events.append({"event_type": event_type, **payload})
        return AnchorResult(
            "confirmed",
            anchor_id=f"{self.prefix}-{event_type}-{len(self.events)}",
        )


def _sessionmakers(
    tmp_path: Path,
) -> tuple[
    sessionmaker,
    sessionmaker,
    sessionmaker,
]:
    database_path = tmp_path / "capability-mount-concurrency.sqlite"
    url = f"sqlite:///{database_path}"

    def make_engine():
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 5},
            poolclass=NullPool,
        )

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine

    engine_a = make_engine()
    engine_b = make_engine()
    engine_c = make_engine()
    Base.metadata.create_all(engine_a)
    with engine_a.begin() as connection:
        connection.execute(
            text(
                "INSERT OR IGNORE INTO merkle_leaf_sequence "
                "(id, next_value) VALUES (1, 0)"
            )
        )
    return (
        sessionmaker(bind=engine_a, autoflush=False, expire_on_commit=False),
        sessionmaker(bind=engine_b, autoflush=False, expire_on_commit=False),
        sessionmaker(bind=engine_c, autoflush=False, expire_on_commit=False),
    )


def _seed_mount(session, *, mount_id: str, token_id: str, nonce: str) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        CapabilityMount(
            mount_id=mount_id,
            token_id=token_id,
            token_nonce=nonce,
            owner_principal="caller",
            owner_workspace="workspace",
            mount_json={},
            token_json={},
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            terminated=False,
            nonce_consumed=False,
        )
    )
    session.commit()


def _package() -> CapabilityPackage:
    suffix = uuid4().hex[:10]
    return CapabilityPackage(
        id=f"nonce-{suffix}@v1",
        family=f"nonce-{suffix}",
        title="Nonce concurrency proof",
        purpose="Prove atomic compare-and-consume",
        reads=["contact.read"],
        policy_defaults={"mode": "draft_only"},
    )


def test_compare_and_consume_admits_exactly_one_of_two_sessions(tmp_path: Path) -> None:
    factory_a, factory_b, factory_c = _sessionmakers(tmp_path)
    mount_id = "mount-atomic"
    token_id = "token-atomic"
    nonce = "nonce-atomic"

    with factory_a() as session:
        _seed_mount(
            session,
            mount_id=mount_id,
            token_id=token_id,
            nonce=nonce,
        )

    with factory_a() as session_a, factory_b() as session_b:
        admitted_a = _consume_nonce_atomically(session_a, mount_id, token_id, nonce)
        session_a.commit()
        admitted_b = _consume_nonce_atomically(session_b, mount_id, token_id, nonce)
        session_b.commit()

    assert sorted((admitted_a, admitted_b)) == [False, True]

    with factory_c() as session_c:
        row = session_c.execute(
            select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        ).scalar_one()
        assert row.nonce_consumed is True
        assert _consume_nonce_atomically(session_c, mount_id, token_id, nonce) is False
        session_c.commit()


def test_interleaved_evaluations_yield_one_allow_one_replay_deny(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_a, factory_b, factory_c = _sessionmakers(tmp_path)
    package = _package()
    execution_id = f"execution-{uuid4().hex}"
    results: dict[str, tuple[Decision, str, AnchorResult, object | None]] = {}
    triggered = False

    with factory_a() as seed_session:
        registry = MountRegistry(
            db=seed_session,
            anchor=_ConfirmedAnchor("seed"),
        )
        registry.register_package(package)
        record, anchor, reason = registry.request_mount(
            package.id,
            MountScope(workspace="workspace", project="project"),
            role="ephemeral_executor",
            policy=MountPolicy(),
            ttl_seconds=300,
            owner_principal="caller",
            owner_workspace="workspace",
            execution_id=execution_id,
        )
        assert record is not None
        assert anchor.status == "confirmed"
        assert reason == "mounted"
        mount_id = record.mount.id
        token_id = record.token.token_id
        nonce = record.token.nonce

    import cappo_backend.capability_mount.service as service_module

    original_consume = service_module._consume_nonce_atomically

    def interleave_before_a_consume(
        db,
        candidate_mount_id: str,
        candidate_token_id: str,
        candidate_nonce: str,
    ) -> bool:
        nonlocal triggered
        if not triggered:
            triggered = True
            with factory_b() as session_b:
                registry_b = MountRegistry(
                    db=session_b,
                    anchor=_ConfirmedAnchor("b"),
                )
                registry_b.register_package(package)
                results["b"] = registry_b.evaluate(
                    mount_id,
                    "contact.read",
                    token_id=token_id,
                    nonce=nonce,
                    owner_principal="caller",
                    owner_workspace="workspace",
                )
        return original_consume(
            db,
            candidate_mount_id,
            candidate_token_id,
            candidate_nonce,
        )

    monkeypatch.setattr(
        service_module,
        "_consume_nonce_atomically",
        interleave_before_a_consume,
    )

    with factory_a() as session_a:
        registry_a = MountRegistry(
            db=session_a,
            anchor=_ConfirmedAnchor("a"),
        )
        registry_a.register_package(package)
        results["a"] = registry_a.evaluate(
            mount_id,
            "contact.read",
            token_id=token_id,
            nonce=nonce,
            owner_principal="caller",
            owner_workspace="workspace",
        )

    assert set(results) == {"a", "b"}
    assert sorted(decision.value for decision, *_ in results.values()) == [
        "allow",
        "deny",
    ]
    assert sorted(reason for _, reason, *_ in results.values()) == [
        "allowed",
        "token_replay",
    ]

    with factory_c() as session_c:
        receipts = session_c.execute(
            select(CapabilityActionReceipt).where(
                CapabilityActionReceipt.token_id == token_id,
                CapabilityActionReceipt.decision == "allow",
            )
        ).scalars().all()
        assert len(receipts) == 1
