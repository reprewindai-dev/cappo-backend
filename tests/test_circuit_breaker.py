"""Circuit breaker + ResilientExecutor tests.

Covers the state machine (open after threshold, reject while open, half-open
recovery, re-open on failed probe) and the executor failover / fail-closed
behaviour. A fake clock drives recovery timing deterministically.
"""

from __future__ import annotations

import pytest

from cappo_backend.services.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)
from cappo_backend.services.executor import (
    EchoExecutor,
    ExecutorUnavailableError,
    Provider,
    ResilientExecutor,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BoomExecutor:
    """Executor that always raises."""

    provider = "boom"

    def execute(self, request):
        raise RuntimeError("provider down")


# --------------------------------------------------------------------------
# CircuitBreaker state machine
# --------------------------------------------------------------------------


def test_starts_closed_and_allows_requests():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.state is CircuitState.CLOSED
    assert cb.allows_request()


def test_opens_after_failure_threshold():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED  # not yet
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    assert not cb.allows_request()


def test_success_resets_failure_count_while_closed():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # resets
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED


def test_rejects_calls_fast_while_open():
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure()
    assert cb.is_open
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: "should not run")


def test_transitions_to_half_open_after_recovery_timeout():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, time_fn=clock)
    cb.record_failure()
    assert cb.state is CircuitState.OPEN

    clock.advance(29.0)
    assert cb.state is CircuitState.OPEN  # not elapsed yet
    clock.advance(1.0)
    assert cb.state is CircuitState.HALF_OPEN
    assert cb.allows_request()  # probe allowed


def test_half_open_success_closes_circuit():
    clock = FakeClock()
    cb = CircuitBreaker(
        failure_threshold=1, recovery_timeout=10.0, success_threshold=2, time_fn=clock
    )
    cb.record_failure()
    clock.advance(10.0)
    assert cb.state is CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state is CircuitState.HALF_OPEN  # needs 2
    cb.record_success()
    assert cb.state is CircuitState.CLOSED


def test_half_open_failure_reopens_circuit():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0, time_fn=clock)
    cb.record_failure()
    clock.advance(10.0)
    assert cb.state is CircuitState.HALF_OPEN
    cb.record_failure()  # failed probe
    assert cb.state is CircuitState.OPEN
    # And the recovery window restarts from the re-open instant.
    clock.advance(9.0)
    assert cb.state is CircuitState.OPEN


def test_call_records_outcomes():
    cb = CircuitBreaker(failure_threshold=2)
    assert cb.call(lambda: 42) == 42
    with pytest.raises(RuntimeError):
        cb.call(_raise)
    with pytest.raises(RuntimeError):
        cb.call(_raise)
    assert cb.is_open


def _raise():
    raise RuntimeError("boom")


# --------------------------------------------------------------------------
# ResilientExecutor failover / fail-closed
# --------------------------------------------------------------------------


def _provider(name, executor, **kwargs):
    return Provider(name=name, executor=executor, breaker=CircuitBreaker(name=name, **kwargs))


def test_primary_serves_when_healthy():
    primary = _provider("primary", EchoExecutor())
    fallback = _provider("fallback", BoomExecutor())
    ex = ResilientExecutor([primary, fallback])
    out = ex.execute({"prompt": "hi", "pgl_id": "test-user-id"})
    assert out["response"] == "echo: hi"


def test_ordinary_primary_error_does_not_authorize_fallback():
    primary = _provider("primary", BoomExecutor())
    fallback = _provider("fallback", EchoExecutor())
    ex = ResilientExecutor([primary, fallback])
    with pytest.raises(ExecutorUnavailableError, match="verified 503"):
        ex.execute({"prompt": "hey"})
    assert fallback.breaker.state is CircuitState.CLOSED


def test_primary_breaker_trips_then_cannot_authorize_fallback():
    primary_breaker = CircuitBreaker(name="primary", failure_threshold=1)
    primary = Provider("primary", BoomExecutor(), primary_breaker)
    fallback = _provider("fallback", EchoExecutor())
    ex = ResilientExecutor([primary, fallback])

    with pytest.raises(ExecutorUnavailableError, match="verified 503"):
        ex.execute({"prompt": "1"})
    assert primary_breaker.is_open
    # An open circuit is not a signed Provider A 503 and must not authorize B.
    with pytest.raises(ExecutorUnavailableError, match="circuit open"):
        ex.execute({"prompt": "2"})
    assert fallback.breaker.state is CircuitState.CLOSED


def test_halts_fail_closed_when_all_providers_unavailable():
    primary = _provider("primary", BoomExecutor(), failure_threshold=1)
    fallback = _provider("fallback", BoomExecutor(), failure_threshold=1)
    ex = ResilientExecutor([primary, fallback])
    with pytest.raises(ExecutorUnavailableError):
        ex.execute({"prompt": "x"})


def test_recovers_after_cooldown():
    clock = FakeClock()
    flip = {"down": True}

    class Flaky:
        provider = "flaky"

        def execute(self, request):
            if flip["down"]:
                raise RuntimeError("down")
            return {"response": "ok", "provider": "flaky", "model": "f", "tokens": 1}

    breaker = CircuitBreaker(
        name="flaky", failure_threshold=1, recovery_timeout=5.0, time_fn=clock
    )
    ex = ResilientExecutor([Provider("flaky", Flaky(), breaker)])

    with pytest.raises(ExecutorUnavailableError):
        ex.execute({"prompt": "a"})  # trips open
    assert breaker.is_open

    flip["down"] = False
    clock.advance(5.0)  # cooldown elapsed -> half-open probe allowed
    out = ex.execute({"prompt": "b"})
    assert out["response"] == "ok"
    assert breaker.state is CircuitState.CLOSED


def test_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        ResilientExecutor([])
