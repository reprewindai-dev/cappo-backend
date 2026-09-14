#!/usr/bin/env python3
import os
import sys
import time
import json
import ctypes
from pathlib import Path
from multiprocessing import Process

try:
    from bcc import BPF
except ImportError:
    print("Error: bcc module not found. Run as root with python3-bpfcc installed.")
    sys.exit(1)

VEKLOM_CGROUP = "/sys/fs/cgroup/veklom_e1"
BPF_SOURCE_FILE = Path(__file__).parent / "linux_evidence_1.bpf.c"

def get_cgroup_id(path: str) -> int:
    return os.stat(path).st_ino

def setup_cgroup():
    os.makedirs(VEKLOM_CGROUP, exist_ok=True)
    return get_cgroup_id(VEKLOM_CGROUP)

def teardown_cgroup():
    try:
        procs = Path(f"{VEKLOM_CGROUP}/cgroup.procs").read_text().splitlines()
        for p in procs:
            try:
                Path("/sys/fs/cgroup/cgroup.procs").write_text(p.strip())
            except OSError:
                pass
        os.rmdir(VEKLOM_CGROUP)
    except OSError:
        pass

def worker(count: int):
    for _ in range(count):
        os.system("true")
    os._exit(0)

def generate_execs(count: int, concurrent: bool):
    """Generate exec events."""
    Path(f"{VEKLOM_CGROUP}/cgroup.procs").write_text(str(os.getpid()))
    if not concurrent:
        for _ in range(count):
            os.system("true")
    else:
        pids = []
        for _ in range(5):
            pid = os.fork()
            if pid == 0:
                worker(count)
            pids.append(pid)
        for pid in pids:
            os.waitpid(pid, 0)

def run_test(mode: str) -> dict:
    cgroup_id = setup_cgroup()
    
    src = BPF_SOURCE_FILE.read_text()
    
    if mode == "E1-A":
        pages = 64
        count = 100
        concurrent = False
        sleep_time = 0
    elif mode == "E1-B":
        pages = 1  # Very small ring (4KB)
        count = 100 # 5 workers * 100 = 500 loops, each doing os.system (which forks sh then execs true = 2 execs). Total ~1000 execs.
        concurrent = True
        sleep_time = 0.05 # Deliberately slow consumer
    else:
        raise ValueError("Unknown mode")
        
    src = src.replace("RING_PAGES", str(pages))
    
    b = BPF(text=src)
    
    # Configure target cgroup
    b["config_cgroup_id"][0] = ctypes.c_uint64(cgroup_id)
    
    delivered = 0
    def handle_event(ctx, data, size):
        nonlocal delivered
        delivered += 1
        if sleep_time > 0:
            time.sleep(sleep_time)
            
    b["events"].open_ring_buffer(handle_event)
    
    gen_pid = os.fork()
    if gen_pid == 0:
        generate_execs(count, concurrent)
        os._exit(0)
    
    # Poll while generating
    import select
    while True:
        pid, status = os.waitpid(gen_pid, os.WNOHANG)
        if pid == gen_pid:
            break
        b.ring_buffer_poll(timeout=100)
        if sleep_time == 0:
            time.sleep(0.01)
            
    # Final drain
    try:
        b.ring_buffer_poll(timeout=100)
    except Exception:
        pass
    
    # Read counters (PERCPU_ARRAY returns an array of values, one per CPU, for key 0)
    seen_total = sum(b["seen_total"][0]) if 0 in b["seen_total"] else 0
    submitted_total = sum(b["submitted_total"][0]) if 0 in b["submitted_total"] else 0
    reserve_failures = sum(b["reserve_failures"][0]) if 0 in b["reserve_failures"] else 0
    
    invariant_holds = (seen_total == submitted_total + reserve_failures)
    
    if mode == "E1-A":
        verdict = "VALID" if (invariant_holds and reserve_failures == 0 and delivered >= count) else "INVALID"
    elif mode == "E1-B":
        if reserve_failures > 0 and invariant_holds:
            verdict = "VALID"
        elif reserve_failures == 0:
            verdict = "INDETERMINATE"
        else:
            verdict = "INVALID"
            
    teardown_cgroup()
    
    return {
        "proof": mode,
        "verdict": verdict,
        "target_cgroup_id": cgroup_id,
        "seen_total": seen_total,
        "submitted_total": submitted_total,
        "reserve_failures": reserve_failures,
        "delivered_to_userspace": delivered,
        "invariant_holds": invariant_holds,
        "security_property": "Every matching kernel event is either represented by a submitted record or accounted for by a measured loss counter"
    }

def main():
    print(json.dumps(run_test("E1-A"), indent=2))
    sys.stdout.flush()
    print(json.dumps(run_test("E1-B"), indent=2))
    sys.stdout.flush()

if __name__ == "__main__":
    main()
