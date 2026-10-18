"""Run state machine.

Replaces the old post-hoc, status-derived lifecycle (migration note §3) with an
explicit pre-execution state machine. The EI mint and EAT mint sit on the
``COMMITTED -> EI_MINTED -> EAT_MINTED`` edge, strictly after governance/commit
and before routing (EI Plan §Mint point, Trust Contract §5):

    CREATED -> COMPILED -> CONTEXTUALIZED -> GOVERNED -> COMMITTED
            -> EI_MINTED -> EAT_MINTED -> ROUTED -> EXECUTING -> EXECUTED -> ATTESTED

Any failure transitions to FAILED.
"""

from __future__ import annotations

from enum import Enum


class RunState(str, Enum):
    CREATED = "CREATED"
    COMPILED = "COMPILED"
    CONTEXTUALIZED = "CONTEXTUALIZED"
    GOVERNED = "GOVERNED"
    COMMITTED = "COMMITTED"
    EI_MINTED = "EI_MINTED"
    EAT_MINTED = "EAT_MINTED"
    ROUTED = "ROUTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    ATTESTED = "ATTESTED"
    FAILED = "FAILED"


# Allowed forward transitions.
_ALLOWED: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.COMPILED, RunState.FAILED},
    RunState.COMPILED: {RunState.CONTEXTUALIZED, RunState.FAILED},
    RunState.CONTEXTUALIZED: {RunState.GOVERNED, RunState.FAILED},
    RunState.GOVERNED: {RunState.COMMITTED, RunState.FAILED},
    RunState.COMMITTED: {RunState.EI_MINTED, RunState.FAILED},
    RunState.EI_MINTED: {RunState.EAT_MINTED, RunState.ROUTED, RunState.FAILED},
    RunState.EAT_MINTED: {RunState.ROUTED, RunState.FAILED},
    RunState.ROUTED: {RunState.EXECUTING, RunState.FAILED},
    RunState.EXECUTING: {RunState.EXECUTED, RunState.FAILED},
    RunState.EXECUTED: {RunState.ATTESTED, RunState.FAILED},
    RunState.ATTESTED: set(),
    RunState.FAILED: set(),
}


class InvalidTransitionError(RuntimeError):
    pass


def assert_transition(current: RunState, target: RunState) -> None:
    if target not in _ALLOWED.get(current, set()):
        raise InvalidTransitionError(f"illegal run transition: {current.value} -> {target.value}")
