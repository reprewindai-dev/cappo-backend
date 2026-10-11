"""VDB-C1.0 normative state machine (PROTOCOL.md). No physical mutation."""

import pytest

from vdb_c1 import states as S


def test_only_finalized_asserts_a_consequence_and_terminals_are_closed():
    assert S.TERMINAL == {S.ADMISSION_REJECTED, S.PRECONDITION_REJECTED, S.ABORTED, S.FINALIZED}
    for t in S.TERMINAL:
        assert S.TRANSITIONS[t] == frozenset()


@pytest.mark.parametrize("path, end", [
    ([S.PREPARED, S.ABORTED], S.ABORTED),
    ([S.PREPARED, S.COMMITTING, S.COMMITTED_RECORD, S.FINALIZED], S.FINALIZED),
    ([S.PREPARED, S.COMMITTING, S.COMMIT_UNCERTAIN, S.ABORTED], S.ABORTED),
    ([S.PREPARED, S.COMMITTING, S.COMMIT_UNCERTAIN, S.COMMITTED_RECORD, S.FINALIZED], S.FINALIZED),
    ([S.PREPARED, S.COMMITTING, S.COMMIT_UNCERTAIN, S.COMMIT_UNCERTAIN], S.COMMIT_UNCERTAIN),
])
def test_legal_paths(path, end):
    assert S.state_after(path) == end


@pytest.mark.parametrize("path", [
    [S.COMMITTING],                                   # no PREPARED first
    [S.PREPARED, S.FINALIZED],                        # finality without commit
    [S.PREPARED, S.COMMITTING, S.FINALIZED],          # FINALIZED without COMMITTED record
    [S.PREPARED, S.COMMITTING, S.ABORTED],            # abort from COMMITTING must go through recovery
    [S.PREPARED, S.COMMITTED_RECORD],                 # storage outcome before the transaction
    [S.PREPARED, S.COMMITTING, S.COMMITTED_RECORD, S.COMMITTED_RECORD],
    [S.PREPARED, S.COMMITTING, S.COMMIT_UNCERTAIN, S.COMMITTED_RECORD, S.ABORTED],
    [S.PREPARED, S.COMMITTING, S.COMMITTED_RECORD, S.FINALIZED, S.FINALIZED],
    [S.PREPARED, S.ABORTED, S.COMMITTING],
    [S.PREPARED, S.PREPARED],
])
def test_illegal_paths(path):
    with pytest.raises(S.IllegalTransition):
        S.state_after(path)
