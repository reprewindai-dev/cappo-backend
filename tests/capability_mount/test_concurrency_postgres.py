from __future__ import annotations

import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    MountPolicy,
    MountScope,
    UnmountReason,
)
from cappo_backend.capability_mount.service import AnchorResult, MountRegistry


class ConfirmedAnchor:
    def anchor(self, event_type: str, **_: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id=f"pg-{event_type}-{uuid4().hex}")


def _postgres_session_factory():
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("row-lock concurrency test requires PostgreSQL DATABASE_URL")
    engine = create_engine(url, pool_pre_ping=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _package() -> CapabilityPackage:
    suffix = uuid4().hex[:10]
    return CapabilityPackage(
        id=f"concurrency-{suffix}@v1",
        family=f"concurrency-{suffix}",
        title="Concurrency probe",
        purpose="Prove SELECT FOR UPDATE serialization",
        reads=["contact.read"],
        policy_defaults={"mode": "draft_only"},
    )


def _new_mount(factory) -> tuple[str, str, str]:
    package = _package()
    with factory() as session:
        registry = MountRegistry(db=session, anchor=ConfirmedAnchor())
        registry.register_package(package)
        record, anchor, reason = registry.request_mount(
            package.id,
            MountScope(workspace="row-lock-workspace", project="row-lock-project"),
            role="ephemeral_executor",
            policy=MountPolicy(),
            ttl_seconds=300,
        )
        assert anchor.status == "confirmed"
        assert reason == "mounted"
        assert record is not None
        return record.mount.id, record.token.token_id, record.token.nonce


def test_same_nonce_has_one_winner_under_postgres_row_lock() -> None:
    factory = _postgres_session_factory()
    mount_id, token_id, nonce = _new_mount(factory)
    barrier = threading.Barrier(2)
    results: list[tuple[Decision, str]] = []
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            with factory() as session:
                registry = MountRegistry(db=session, anchor=ConfirmedAnchor())
                barrier.wait(timeout=10)
                decision, reason, _, _ = registry.evaluate(
                    mount_id,
                    "contact.read",
                    token_id=token_id,
                    nonce=nonce,
                )
                results.append((decision, reason))
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            failures.append(exc)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert failures == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(decision.value for decision, _ in results) == ["allow", "deny"]
    assert sorted(reason for _, reason in results) == ["allowed", "token_replay"]


def test_concurrent_termination_is_idempotent_and_blocks_later_action() -> None:
    factory = _postgres_session_factory()
    mount_id, token_id, nonce = _new_mount(factory)
    barrier = threading.Barrier(2)
    results: list[tuple[Decision, str]] = []
    failures: list[BaseException] = []

    def terminate_worker() -> None:
        try:
            with factory() as session:
                registry = MountRegistry(db=session, anchor=ConfirmedAnchor())
                barrier.wait(timeout=10)
                decision, reason, _ = registry.terminate(
                    mount_id,
                    UnmountReason.EXPLICIT_TERMINATE,
                )
                results.append((decision, reason))
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            failures.append(exc)

    threads = [
        threading.Thread(target=terminate_worker),
        threading.Thread(target=terminate_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert failures == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(decision.value for decision, _ in results) == ["allow", "allow"]
    assert sorted(reason for _, reason in results) == ["already_terminated", "terminated"]

    with factory() as session:
        registry = MountRegistry(db=session, anchor=ConfirmedAnchor())
        decision, reason, _, _ = registry.evaluate(
            mount_id,
            "contact.read",
            token_id=token_id,
            nonce=nonce,
        )
        assert decision is Decision.DENY
        assert reason == "terminated"
