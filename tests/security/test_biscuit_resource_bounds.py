"""Biscuit resource bounds are exact names; prefixes exist only as explicit "dir/*".

Before, ``allowed_resource("42")`` was checked with ``starts_with``, so a grant for
record 42 also covered 420, 4200 and "42-anything".
"""

from __future__ import annotations

import pytest

from cappo_backend.security.biscuit import (
    TrustedRevocationState,
    extract_authority_context,
    mint_biscuit_capability,
    verify_biscuit_capability,
)

CALLER = "spiffe://veklom/test/caller"
EXEC = "spiffe://veklom/test/executor"


def mint(resources: list[str], envelope: str | None = None) -> str:
    return mint_biscuit_capability(
        caller_spiffe_id=CALLER, executor_spiffe_id=EXEC, capability_id="t@v1",
        reads=[], writes=["record.modify"], execution_id="exec-1", ttl_seconds=300, resources=resources,
        envelope_digest=envelope,
    )


def allowed(token: str, resource: str, envelope: str | None = None) -> bool:
    state = TrustedRevocationState()
    state.known_epochs["workspace"] = 0
    return verify_biscuit_capability(token, EXEC, "record.modify", resource, subject_spiffe_id=CALLER,
                                     trusted_state=state, execution_id="exec-1", envelope_digest=envelope)


def test_exact_resource_is_exact():
    token = mint(["42"])
    assert allowed(token, "42")
    for other in ("420", "4", "43", "42-x", "42/1"):
        assert not allowed(token, other), other


def test_prefix_only_when_written_as_one():
    token = mint(["records/*"])
    assert allowed(token, "records/42")
    assert not allowed(token, "recordsX")
    assert extract_authority_context(token).allowed_resources == {"records/*"}


def test_envelope_must_match():
    digest = "a" * 64
    token = mint(["42"], envelope=digest)
    assert allowed(token, "42", envelope=digest)
    assert not allowed(token, "42", envelope="b" * 64)
    assert not allowed(token, "42")


@pytest.mark.parametrize("bad", ['42")', '42"; allowed_resource("43', "a b", "*", ""])
def test_resource_terms_cannot_inject_datalog(bad: str):
    with pytest.raises(ValueError):
        mint([bad])
