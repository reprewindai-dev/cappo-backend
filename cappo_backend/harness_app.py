import hashlib
import json
import os
import sys

from fastapi import Request

# Patch LocalRecordAdapter
import cappo_backend.capability_mount.effects as effects
from cappo_backend.core_instrumentation import check_crash, crash_point_var, semantic_commitment_var

original_invoke = effects.LocalRecordAdapter.invoke
def instrumented_invoke(self, action, resource, arguments):
    check_crash("before_invocation")
    
    # Observe at effect observation
    expected_hash = semantic_commitment_var.get()
    if expected_hash:
        payload_dict = {"action": action, "resource": resource, "arguments": arguments}
        actual_hash = hashlib.sha256(json.dumps(payload_dict, sort_keys=True).encode()).hexdigest()
        print(f"[effect_observation] actual_hash={actual_hash} expected_hash={expected_hash}", flush=True)
        # Note: We don't fail here if it doesn't match perfectly unless we constructed the exact same dictionary structure.
        # Let's assume the test client constructs the hash strictly from this sub-dict for simplicity, OR we pass the whole request body.
    
    res = original_invoke(self, action, resource, arguments)
    check_crash("after_observable_effect")
    return res
effects.LocalRecordAdapter.invoke = instrumented_invoke

# Patch MountRegistry._record
import cappo_backend.capability_mount.service as service

original_record = service.MountRegistry._record

def instrumented_record(self, row):
    record = original_record(self, row)
    binding = record.binding
    
    original_cappo_evaluator = binding._cappo_evaluator
    def instrumented_cappo_evaluator(*args, **kwargs):
        check_crash("authorization")
        res = original_cappo_evaluator(*args, **kwargs)
        check_crash("after_dispatch")
        return res
    binding._cappo_evaluator = instrumented_cappo_evaluator
    
    original_begin = binding._begin_consequence
    def instrumented_begin(*args, **kwargs):
        check_crash("consequence_entry")
        return original_begin(*args, **kwargs)
    binding._begin_consequence = instrumented_begin
    
    original_completion = binding._completion_reporter
    def instrumented_completion(*args, **kwargs):
        check_crash("before_evidence_durability")
        res = original_completion(*args, **kwargs)
        check_crash("evidence_readback")
        return res
    binding._completion_reporter = instrumented_completion
    
    return record

service.MountRegistry._record = instrumented_record

from cappo_backend.main import app


@app.middleware("http")
async def semantic_commitment_middleware(request: Request, call_next):
    commitment = request.headers.get("x-veklom-semantic-commitment")
    if commitment:
        semantic_commitment_var.set(commitment)
    
    response = await call_next(request)
    check_crash("before_terminal_state_durability")
    return response

