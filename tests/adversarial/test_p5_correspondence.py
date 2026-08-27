"""
P5 Correspondence Tests
=======================
Executes the Wave 2 adversarial tests, captures the truth-state traces,
and verifies them against the TLA+ correspondence model.
"""
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from cappo_backend.formal.p5_correspondence import (
    TruthTransitionTrace,
    extract_traces,
    verify_trace,
    verify_no_model_gap,
    verify_mutation_surface,
    KNOWN_MUTATION_SITES,
    CorrespondenceViolation,
)

def test_p5_formal_mutation_surface():
    """Verify there are no unmodeled truth-state mutation sites in service.py."""
    gaps = verify_no_model_gap()
    assert not gaps, f"TLA+ actions with no Python binding: {gaps}"
    
    unregistered = verify_mutation_surface("cappo_backend/capability_mount/service.py")
    assert not unregistered, f"Unregistered mutation sites found: {unregistered}"

def test_p5_trace_correspondence(db):
    """
    Run adversarial tests and ensure every truth state mutation 
    maps to a legal TLA+ action.
    """
    # 1. Hook the DB session to intercept every db.add(ce)
    # We inspect the stack to determine the actor_class
    actor_map: dict[tuple[str, int], str] = {}
    
    def on_before_flush(session, flush_context, instances):
        import inspect
        for obj in session.new:
            if isinstance(obj, ConsequenceExecutionEvent):
                # Find actor by inspecting the stack
                actor = "unknown"
                for frame in inspect.stack():
                    if frame.function in KNOWN_MUTATION_SITES:
                        actor = frame.function
                        break
                    elif frame.function.startswith("test_"):
                        actor = "direct_orm_bypass"
                        break
                
                actor_map[(obj.operation_id, obj.version)] = actor
                
    event.listen(db, "before_flush", on_before_flush)
    
    # 2. Execute T16-T21 from wave 2 to generate some traces
    import tests.adversarial.test_p5_wave2_truth_authority as w2
    try:
        w2.test_t18_concurrent_reconciler_race(db)
    except AssertionError: pass
    except Exception: pass
    
    try:
        w2.test_t6_proof_transplantation_wrong_operation(db)
    except AssertionError: pass
    except Exception: pass
    
    try:
        w2.test_t14_executor_cannot_resolve_unknown(db)
    except Exception: pass
    
    # 3. Verify traces
    from sqlalchemy import select
    all_events = db.execute(select(ConsequenceExecutionEvent)).scalars().all()
    
    # Group by operation_id
    by_op = {}
    for ev in all_events:
        by_op.setdefault(ev.operation_id, []).append(ev)
        
    assert len(by_op) > 0, "No traces generated!"
    
    verified_count = 0
    for op_id, events in by_op.items():
        traces = extract_traces(events, actor_map)
        for t in traces:
            try:
                tla_action = verify_trace(t)
            except CorrespondenceViolation as e:
                if "direct_orm_bypass" in t.actor_class and "hostile TLA+ action" in str(e):
                    print(f"Verified hostile block: {e}")
                    continue
                raise
            print(f"Verified transition: {t.previous_truth_state} -> {t.next_truth_state} via {tla_action}")
            verified_count += 1
            
    assert verified_count >= 5, "Not enough traces verified."
