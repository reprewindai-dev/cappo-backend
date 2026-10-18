import pytest

from cappo_backend.services.run_state import InvalidTransitionError, RunState, assert_transition


def test_valid_transition():
    """Test valid sequential transitions."""
    assert_transition(RunState.CREATED, RunState.COMPILED)
    assert_transition(RunState.COMPILED, RunState.CONTEXTUALIZED)
    assert_transition(RunState.CONTEXTUALIZED, RunState.GOVERNED)
    assert_transition(RunState.GOVERNED, RunState.COMMITTED)
    assert_transition(RunState.COMMITTED, RunState.EI_MINTED)
    assert_transition(RunState.EI_MINTED, RunState.EAT_MINTED)
    assert_transition(RunState.EAT_MINTED, RunState.ROUTED)
    assert_transition(RunState.ROUTED, RunState.EXECUTING)
    assert_transition(RunState.EXECUTING, RunState.EXECUTED)
    assert_transition(RunState.EXECUTED, RunState.ATTESTED)


def test_valid_transition_to_failed():
    """Test valid transitions to FAILED from any non-terminal state."""
    for state in RunState:
        if state not in (RunState.ATTESTED, RunState.FAILED):
            assert_transition(state, RunState.FAILED)


def test_invalid_transition():
    """Test invalid transitions like jumping states, going backwards, or staying in the same state."""
    # Jumping states
    with pytest.raises(InvalidTransitionError, match="illegal run transition: CREATED -> CONTEXTUALIZED"):
        assert_transition(RunState.CREATED, RunState.CONTEXTUALIZED)

    # Going backwards
    with pytest.raises(InvalidTransitionError, match="illegal run transition: COMPILED -> CREATED"):
        assert_transition(RunState.COMPILED, RunState.CREATED)

    # Same state
    with pytest.raises(InvalidTransitionError, match="illegal run transition: CREATED -> CREATED"):
        assert_transition(RunState.CREATED, RunState.CREATED)


def test_invalid_transition_from_terminal():
    """Test that no transitions are allowed from terminal states."""
    # From ATTESTED
    for state in RunState:
        with pytest.raises(InvalidTransitionError, match=f"illegal run transition: ATTESTED -> {state.value}"):
            assert_transition(RunState.ATTESTED, state)

    # From FAILED
    for state in RunState:
        with pytest.raises(InvalidTransitionError, match=f"illegal run transition: FAILED -> {state.value}"):
            assert_transition(RunState.FAILED, state)
