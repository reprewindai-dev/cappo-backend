import sys
import os
import time
import json
import uuid
import ctypes
import hashlib
from datetime import datetime, timezone
from bcc import BPF

BPF_CODE = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>
#include <linux/bpf.h>

struct lifecycle_event_t {
    u32 event_type; // 1 = rmdir, 2 = mark_victim, 3 = exec
    u32 pid;
    u64 cgroup_id;
    char comm[16];
    u64 timestamp_ns;
};

BPF_RINGBUF_OUTPUT(events, 64);
BPF_ARRAY(config, u64, 1);
BPF_ARRAY(loss_counter, u64, 1);

TRACEPOINT_PROBE(cgroup, cgroup_rmdir) {
    int key = 0;
    u64 *target_cg = config.lookup(&key);
    if (!target_cg) return 0;

    u64 rm_cg_id = args->id;
    if (rm_cg_id == *target_cg) {
        struct lifecycle_event_t *ev = events.ringbuf_reserve(sizeof(struct lifecycle_event_t));
        if (!ev) {
            u64 *drops = loss_counter.lookup(&key);
            if (drops) __sync_fetch_and_add(drops, 1);
            return 0;
        }
        ev->event_type = 1;
        ev->pid = bpf_get_current_pid_tgid() >> 32;
        ev->cgroup_id = rm_cg_id;
        bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
        ev->timestamp_ns = bpf_ktime_get_ns();
        events.ringbuf_submit(ev, 0);
    }
    return 0;
}

TRACEPOINT_PROBE(oom, mark_victim) {
    int key = 0;
    u64 *target_cg = config.lookup(&key);
    if (!target_cg) return 0;

    u64 current_cg = bpf_get_current_cgroup_id();
    struct lifecycle_event_t *ev = events.ringbuf_reserve(sizeof(struct lifecycle_event_t));
    if (!ev) {
        u64 *drops = loss_counter.lookup(&key);
        if (drops) __sync_fetch_and_add(drops, 1);
        return 0;
    }
    ev->event_type = 2;
    ev->pid = args->pid;
    ev->cgroup_id = current_cg;
    __builtin_memset(&ev->comm, 0, sizeof(ev->comm));
    ev->timestamp_ns = bpf_ktime_get_ns();
    events.ringbuf_submit(ev, 0);
    return 0;
}

TRACEPOINT_PROBE(sched, sched_process_exec) {
    int key = 0;
    u64 *target_cg = config.lookup(&key);
    if (!target_cg) return 0;

    u64 current_cg = bpf_get_current_cgroup_id();
    if (current_cg != *target_cg) return 0;

    struct lifecycle_event_t *ev = events.ringbuf_reserve(sizeof(struct lifecycle_event_t));
    if (!ev) {
        u64 *drops = loss_counter.lookup(&key);
        if (drops) __sync_fetch_and_add(drops, 1);
        return 0;
    }
    ev->event_type = 3;
    ev->pid = bpf_get_current_pid_tgid() >> 32;
    ev->cgroup_id = current_cg;
    bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
    ev->timestamp_ns = bpf_ktime_get_ns();
    events.ringbuf_submit(ev, 0);
    return 0;
}
"""

def setup_cgroup(name):
    cg_path = f"/sys/fs/cgroup/{name}"
    os.makedirs(cg_path, exist_ok=True)
    
    class FileHandle(ctypes.Structure):
        _fields_ = [
            ("handle_bytes", ctypes.c_uint),
            ("handle_type", ctypes.c_int),
            ("f_handle", ctypes.c_ubyte * 8)
        ]
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    AT_FDCWD = -100
    handle = FileHandle()
    handle.handle_bytes = 8
    mnt_id = ctypes.c_int()
    
    ret = libc.name_to_handle_at(AT_FDCWD, cg_path.encode(), ctypes.byref(handle), ctypes.byref(mnt_id), 0)
    if ret != 0:
        raise OSError(f"name_to_handle_at failed: {ctypes.get_errno()}")
    
    cg_id = int.from_bytes(bytes(handle.f_handle), byteorder=sys.byteorder)
    return cg_path, cg_id

def teardown_cgroup(cg_path):
    if os.path.exists(cg_path):
        try:
            os.rmdir(cg_path)
        except OSError:
            pass

def read_memory_events(cg_path):
    events_path = os.path.join(cg_path, "memory.events")
    if not os.path.exists(events_path):
        return {}
    res = {}
    with open(events_path, "r") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) == 2:
                res[parts[0]] = int(parts[1])
    return res

def run_consequence_proof():
    b = BPF(text=BPF_CODE)
    execution_id = f"exec-linux-{uuid.uuid4().hex}"
    cg_name = f"cappo_{execution_id}"
    cg_path, cg_id = setup_cgroup(cg_name)
    
    b["config"][0] = ctypes.c_uint64(cg_id)
    b["loss_counter"][0] = ctypes.c_uint64(0)
    
    events_captured = []
    def handle_event(cpu, data, size):
        event = b["events"].event(data)
        event_name = {1: "TEARDOWN", 2: "OOM_VICTIM", 3: "EXEC"}.get(event.event_type, "UNKNOWN")
        events_captured.append({
            "type": event_name,
            "pid": event.pid,
            "cgroup_id": event.cgroup_id,
            "comm": event.comm.decode('utf-8', 'replace').strip('\x00'),
            "timestamp_ns": event.timestamp_ns
        })
        
    b["events"].open_ring_buffer(handle_event)
    
    # Configure execution envelope (16M hard limit)
    limit_bytes = 16 * 1024 * 1024
    with open(os.path.join(cg_path, "memory.max"), "w") as f:
        f.write(str(limit_bytes))
        
    # Spawn memory hog
    gen_pid = os.fork()
    if gen_pid == 0:
        with open(os.path.join(cg_path, "cgroup.procs"), "w") as f:
            f.write(str(os.getpid()))
        # allocate 32MB string, exceeds 16M
        os.execlp("python3", "python3", "-c", "a = 'x' * (32 * 1024 * 1024); import time; time.sleep(10)")
        os._exit(0)
        
    pid, status = os.waitpid(gen_pid, 0)
    
    # Read native state
    mem_events = read_memory_events(cg_path)
    native_oom_kill = mem_events.get("oom_kill", 0)
    
    # Teardown envelope
    teardown_cgroup(cg_path)
    
    # Drain events
    time.sleep(0.1)
    b.ring_buffer_poll(timeout=100)
    
    # Gather independent loss metrics
    dropped_events = b["loss_counter"][0].value
    
    # Filter mark_victim for our target cgroup
    correlated_events = [e for e in events_captured if e["cgroup_id"] == cg_id]
    
    # Determine outcome
    has_oom = any(e["type"] == "OOM_VICTIM" for e in correlated_events)
    has_exec = any(e["type"] == "EXEC" for e in correlated_events)
    has_teardown = any(e["type"] == "TEARDOWN" for e in correlated_events)
    
    if has_oom and native_oom_kill > 0:
        outcome = "RESOURCE_EXHAUSTED"
    elif status == 0:
        outcome = "SUCCESS"
    else:
        outcome = "FAILED"
        
    # Build Consequence Receipt
    receipt_payload = {
        "version": "1.0",
        "execution_id": execution_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "substrate": "linux-cgroup-v2-ebpf",
        "identity": {
            "cgroup_id": cg_id,
            "cgroup_path": cg_path
        },
        "enforcement": {
            "memory_limit_bytes": limit_bytes,
            "native_oom_kill_count": native_oom_kill
        },
        "outcome": outcome,
        "evidence": {
            "independent_events": correlated_events,
            "event_loss_count": dropped_events
        }
    }
    
    # Seal (hash)
    payload_str = json.dumps(receipt_payload, sort_keys=True)
    digest = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()
    receipt_payload["seal"] = f"sha256:{digest}"
    
    # Validation logic
    valid = (outcome == "RESOURCE_EXHAUSTED" and 
             has_exec and has_teardown and has_oom and 
             dropped_events == 0)
             
    result = {
        "proof": "LINUX-CONSEQUENCE-1",
        "verdict": "VALID" if valid else "INVALID",
        "receipt": receipt_payload
    }
    
    with open("consequence_receipt.json", "w") as f:
        json.dump(result, f, indent=2)
        
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    run_consequence_proof()
