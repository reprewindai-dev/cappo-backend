import contextvars
import hashlib
import json
import os

semantic_commitment_var = contextvars.ContextVar('semantic_commitment', default=None)
crash_point_var = contextvars.ContextVar('crash_point', default=os.environ.get('CRASH_AT', 'never'))

def verify_semantic_commitment(point: str, actual_dict: dict):
    expected_hash = semantic_commitment_var.get()
    if not expected_hash:
        return
        
    normalized = json.dumps(actual_dict, sort_keys=True, separators=(',', ':'))
    actual_hash = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f'Semantic commitment mismatch at {point}:\nExpected: {expected_hash}\nGot: {actual_hash}\nPayload: {normalized}')
    print(f'[{point}] Semantic commitment verified', flush=True)

def check_crash(point: str):
    print(f'[BARRIER] {point}', flush=True)
    if crash_point_var.get() == point:
        print(f'!!! INTENTIONAL CRASH AT {point} !!!', flush=True)
        os._exit(1)
