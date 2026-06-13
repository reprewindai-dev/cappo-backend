"""Circuit breaker for the execution layer.

Migration note §2/§5 names the old backend's circuit-breaker discipline
(``record_failure`` / ``record_success`` / ``is_open``) as a lineage seed to
carry forward. This is a forward-construction of that pattern as a small,
self-contained state machine.

States::

    CLOSED      calls allowed; consecutive failures are counted
      │  (failure_count >= failure_threshold)
      ▼
    OPEN        calls rejected fast (fail-closed) until the recovery timeout
      │  (now - opened_at >= recovery_timeout)
      ▼
    HALF_OPEN   a limited number of probe calls allowed
      ├─ probe succeeds (success_threshold times) ──► CLOSED
      └─ probe fails ───────────────────────────────► OPEN

Fail-closed by design: while OPEN the breaker rejects rather than letting a call
hammer an unhealthy backend. The clock is injectable so behaviour is
deterministically testable without sleeping.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a call is attempted while the breaker is OPEN."""

    def __init__(self, name: str) -> None:
        super().__init__(f"circuit '{name}' is open")
        self.name = name


@dataclass
class CircuitBreaker:
    """A single-resource circuit breaker.

    Parameters
    ----------
    name:
        Identifier (e.g. the provider name) used in errors/logging.
    failure_threshold:
        Consecutive failures in CLOSED that trip the breaker OPEN.
    recovery_timeout:
        Seconds the breaker stays OPEN before allowing HALF_OPEN probes.
    success_threshold:
        Consecutive probe successes in HALF_OPEN required to close again.
    time_fn:
        Monotonic clock source (injectable for tests).
    """

    name: str = "default"
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    success_threshold: int = 1
    time_fn: Callable[[], float] = field(default=time.monotonic)

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _successes: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        """Current state, accounting for an elapsed recovery timeout."""
        if self._state is CircuitState.OPEN and self._recovery_elapsed():
            self._state = CircuitState.HALF_OPEN
            self._successes = 0
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state is CircuitState.OPEN

    def allows_request(self) -> bool:
        """Whether a call may proceed right now (probes allowed in HALF_OPEN)."""
        return self.state is not CircuitState.OPEN

    def record_success(self) -> None:
        if self.state is CircuitState.HALF_OPEN:
            self._successes += 1
            if self._successes >= self.success_threshold:
                self._close()
        else:
            self._failures = 0

    def record_failure(self) -> None:
        # A failed probe in HALF_OPEN immediately re-opens the breaker.
        if self.state is CircuitState.HALF_OPEN:
            self._open()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    def call(self, fn: Callable[[], object]) -> object:
        """Run ``fn`` under the breaker.

        Rejects fast with :class:`CircuitOpenError` when OPEN; otherwise records
        the success/failure outcome and re-raises any exception from ``fn``.
        """
        if not self.allows_request():
            raise CircuitOpenError(self.name)
        try:
            result = fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    # ------------------------------------------------------------------

    def _recovery_elapsed(self) -> bool:
        return (self.time_fn() - self._opened_at) >= self.recovery_timeout

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self.time_fn()
        self._successes = 0

    def _close(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
