import pytest
from cappo_backend.security.biscuit import extract_authority_context, mint_biscuit_capability

def test_extract_authority_context_no_datalog_limit():
    b64 = mint_biscuit_capability(
        caller_spiffe_id="spiffe://veklom/node/1",
        executor_spiffe_id="spiffe://veklom/node/1",
        capability_id="veklom.governed-counter@v1",
        reads=[],
        writes=["counter.increment"],
        execution_id="exec_123",
        ttl_seconds=300,
        resources=["counter"]
    )
    
    ctx = extract_authority_context(b64)
    
    assert ctx is not None
    assert "counter.increment" in ctx.allowed_actions
