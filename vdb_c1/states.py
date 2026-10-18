"""VDB-C1.0 state machine (normative transitions from PROTOCOL.md)."""

from __future__ import annotations

RECEIVED = "RECEIVED"
ADMISSION_REJECTED = "ADMISSION_REJECTED"
ADMITTED = "ADMITTED"
PRECONDITION_REJECTED = "PRECONDITION_REJECTED"
PREPARED = "PREPARED"
ABORTED = "ABORTED"
COMMITTING = "COMMITTING"
COMMIT_UNCERTAIN = "COMMIT_UNCERTAIN"
FINALIZED = "FINALIZED"

TRANSITIONS: dict[str, frozenset[str]] = {
    RECEIVED: frozenset({ADMISSION_REJECTED, ADMITTED}),
    ADMITTED: frozenset({PRECONDITION_REJECTED, PREPARED}),
    PREPARED: frozenset({ABORTED, COMMITTING}),
    COMMITTING: frozenset({FINALIZED, COMMIT_UNCERTAIN}),
    # Recovery's resolution, taken only from the store's durable record.
    COMMIT_UNCERTAIN: frozenset({FINALIZED, ABORTED}),
    ADMISSION_REJECTED: frozenset(),
    PRECONDITION_REJECTED: frozenset(),
    ABORTED: frozenset(),
    FINALIZED: frozenset(),
}

TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)

# Journal record kinds. COMMITTED is not a state: it records the confirmed
# storage outcome and is legal only while COMMITTING or COMMIT_UNCERTAIN.
COMMITTED_RECORD = "COMMITTED"
REJECTION_KINDS = frozenset({ADMISSION_REJECTED, PRECONDITION_REJECTED})
BINDING_KINDS = frozenset({PREPARED, COMMITTING, COMMIT_UNCERTAIN, COMMITTED_RECORD,
                           FINALIZED, ABORTED})


class IllegalTransition(Exception):
    pass


def check(current: str, target: str) -> None:
    if target not in TRANSITIONS[current]:
        raise IllegalTransition(f"{current} -> {target}")


def state_after(kinds: list[str]) -> str:
    """Replay binding-record kinds for one intent; raise on an illegal path."""
    state = RECEIVED
    committed_seen = False
    for kind in kinds:
        if kind == COMMITTED_RECORD:
            if state not in (COMMITTING, COMMIT_UNCERTAIN) or committed_seen:
                raise IllegalTransition(f"COMMITTED record in {state}")
            committed_seen = True
            continue
        if kind == PREPARED and state == RECEIVED:
            check(RECEIVED, ADMITTED)
            state = ADMITTED
        if kind == COMMIT_UNCERTAIN and state == COMMIT_UNCERTAIN:
            continue                            # recovery may note it again
        if kind == FINALIZED and not committed_seen:
            raise IllegalTransition("FINALIZED without a COMMITTED record")
        if kind == ABORTED and committed_seen:
            raise IllegalTransition("ABORTED after a COMMITTED record")
        check(state, kind)
        state = kind
    return state
