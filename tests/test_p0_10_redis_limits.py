"""Adversarial tests for P0-10 (Distributed limits + node limits + kill switches)."""

from __future__ import annotations

import time

import pytest
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cappo_backend.config import Settings
from cappo_backend.db.base import Base
from cappo_backend.services.payment_gate import PaymentGate, PaymentRequiredError


# Setup clean ephemeral memory DB for testing database constraints
@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

class MockRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.should_fail = False

    def _check_fail(self) -> None:
        if self.should_fail:
            raise redis.exceptions.ConnectionError("Redis connection refused.")

    def exists(self, key: str) -> bool:
        self._check_fail()
        return key in self.store

    def get(self, key: str) -> str | None:
        self._check_fail()
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._check_fail()
        self.store[key] = str(value)
        if ex:
            self.expiries[key] = ex
        return True

    def incr(self, key: str) -> int:
        self._check_fail()
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    def decr(self, key: str) -> int:
        self._check_fail()
        val = int(self.store.get(key, 0)) - 1
        self.store[key] = str(val)
        return val

    def incrby(self, key: str, amount: int) -> int:
        self._check_fail()
        val = int(self.store.get(key, 0)) + amount
        self.store[key] = str(val)
        return val

    def expire(self, key: str, seconds: int) -> bool:
        self._check_fail()
        self.expiries[key] = seconds
        return True


def test_payment_gate_hard_kill_switch(db_session) -> None:
    settings = Settings(
        _env_file=None,
        max_runs_per_hour=10,
        max_tokens_per_hour=1000,
        max_node_concurrent_runs=5,
    )
    redis_client = MockRedis()
    redis_client.store["vnp:kill_switch:workspace:tenant-123"] = "true"

    gate = PaymentGate(db_session, redis_client=redis_client, settings=settings)
    
    with pytest.raises(PaymentRequiredError) as exc:
        gate.check("tenant-123")
    
    assert exc.value.reason == "kill_switch"
    assert "Hard kill switch active" in exc.value.detail


def test_payment_gate_workspace_runs_limit(db_session) -> None:
    settings = Settings(
        _env_file=None,
        max_runs_per_hour=5,
        max_tokens_per_hour=1000,
        max_node_concurrent_runs=5,
    )
    redis_client = MockRedis()
    
    # Simulate 5 runs already executed this hour
    hour_timestamp = int(time.time() // 3600)
    redis_client.store[f"cappo:limit:workspace:tenant-123:runs:{hour_timestamp}"] = "5"

    gate = PaymentGate(db_session, redis_client=redis_client, settings=settings)
    
    with pytest.raises(PaymentRequiredError) as exc:
        gate.check("tenant-123")
        
    assert exc.value.reason == "rate_limited"
    assert "Workspace hourly execution limit exceeded" in exc.value.detail


def test_payment_gate_workspace_tokens_limit(db_session) -> None:
    settings = Settings(
        _env_file=None,
        max_runs_per_hour=5,
        max_tokens_per_hour=1000,
        max_node_concurrent_runs=5,
    )
    redis_client = MockRedis()
    
    # Simulate 1000 tokens already used this hour
    hour_timestamp = int(time.time() // 3600)
    redis_client.store[f"cappo:limit:workspace:tenant-123:tokens:{hour_timestamp}"] = "1000"

    gate = PaymentGate(db_session, redis_client=redis_client, settings=settings)
    
    with pytest.raises(PaymentRequiredError) as exc:
        gate.check("tenant-123")
        
    assert exc.value.reason == "rate_limited"
    assert "Workspace hourly token limit exceeded" in exc.value.detail


def test_payment_gate_node_concurrent_runs_limit(db_session) -> None:
    settings = Settings(
        _env_file=None,
        max_runs_per_hour=5,
        max_tokens_per_hour=1000,
        max_node_concurrent_runs=3,
    )
    redis_client = MockRedis()
    
    # Simulate 3 active runs on the node
    redis_client.store["cappo:limit:node:concurrent_runs"] = "3"

    gate = PaymentGate(db_session, redis_client=redis_client, settings=settings)
    
    with pytest.raises(PaymentRequiredError) as exc:
        gate.check("tenant-123")
        
    assert exc.value.reason == "node_limit_exceeded"
    assert "Node concurrent execution limit exceeded" in exc.value.detail
    
    # Verify it decremented the counter back
    assert redis_client.store["cappo:limit:node:concurrent_runs"] == "3"


def test_payment_gate_fail_closed_on_redis_connectivity_lost(db_session) -> None:
    settings = Settings(
        _env_file=None,
        max_runs_per_hour=5,
        max_tokens_per_hour=1000,
        max_node_concurrent_runs=5,
    )
    redis_client = MockRedis()
    redis_client.should_fail = True

    gate = PaymentGate(db_session, redis_client=redis_client, settings=settings)
    
    with pytest.raises(PaymentRequiredError) as exc:
        gate.check("tenant-123")
        
    assert exc.value.reason == "redis_unreachable"
    assert "Redis connectivity error" in exc.value.detail


def test_decrement_concurrent_runs(db_session) -> None:
    redis_client = MockRedis()
    redis_client.store["cappo:limit:node:concurrent_runs"] = "5"
    
    gate = PaymentGate(db_session, redis_client=redis_client)
    gate.decrement_concurrent()
    
    assert redis_client.store["cappo:limit:node:concurrent_runs"] == "4"


def test_record_tokens_usage(db_session) -> None:
    redis_client = MockRedis()
    hour_timestamp = int(time.time() // 3600)
    tokens_key = f"cappo:limit:workspace:tenant-123:tokens:{hour_timestamp}"
    
    gate = PaymentGate(db_session, redis_client=redis_client)
    gate.record_tokens("tenant-123", 500)
    
    assert redis_client.store[tokens_key] == "500"
