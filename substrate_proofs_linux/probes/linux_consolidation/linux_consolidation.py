import sys
import os
import time
import json
import ctypes
from bcc import BPF

def setup_cgroup(name):
    cg_path = f"/sys/fs/cgroup/{name}"
    os.makedirs(cg_path, exist_ok=True)
    class FileHandle(ctypes.Structure):
        _fields_ = [("handle_bytes", ctypes.c_uint), ("handle_type", ctypes.c_int), ("f_handle", ctypes.c_ubyte * 8)]
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    AT_FDCWD = -100
    handle = FileHandle()
    handle.handle_bytes = 8
    mnt_id = ctypes.c_int()
    if libc.name_to_handle_at(AT_FDCWD, cg_path.encode(), ctypes.byref(handle), ctypes.byref(mnt_id), 0) != 0:
        raise OSError("name_to_handle_at failed")
    cg_id = int.from_bytes(bytes(handle.f_handle), byteorder=sys.byteorder)
    return cg_path, cg_id

def get_start_time(pid):
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            stat = f.read().split()
            # starttime is the 22nd field (0-indexed 21)
            return int(stat[21])
    except FileNotFoundError:
        return None

def open_pidfd(pid):
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    # syscall 434 is pidfd_open on x86_64
    SYS_pidfd_open = 434
    fd = libc.syscall(SYS_pidfd_open, pid, 0)
    return fd

def main():
    execution_id = "exec-consolidation-001"
    
    # 1. & 2. execution_id -> cgroup identity
    cg_path, cg_id = setup_cgroup(execution_id)
    
    # 3. native resource envelope
    with open(os.path.join(cg_path, "memory.max"), "w") as f:
        f.write("16M")
        
    bpf = BPF(src_file="linux_consolidation.bpf.c")
    bpf["config"][0] = ctypes.c_uint64(cg_id)
    
    events = []
    def handle_event(cpu, data, size):
        ev = bpf["events"].event(data)
        t = {1: "EXEC", 2: "OOM", 3: "TEARDOWN"}.get(ev.type, "UNKNOWN")
        events.append({"type": t, "pid": ev.pid, "cgroup_id": ev.cgroup_id, "timestamp_ns": ev.timestamp_ns})
    bpf["events"].open_ring_buffer(handle_event)
    
    # 4. PID/pidfd/pidns -> native execution
    pid = os.fork()
    if pid == 0:
        with open(os.path.join(cg_path, "cgroup.procs"), "w") as f:
            f.write(str(os.getpid()))
        os.execlp("python3", "python3", "-c", "a = 'x' * (32 * 1024 * 1024); import time; time.sleep(10)")
        os._exit(0)
        
    pidfd = open_pidfd(pid)
    start_time = get_start_time(pid)
    
    os.waitpid(pid, 0)
    
    native_events = {}
    with open(os.path.join(cg_path, "memory.events"), "r") as f:
        for line in f:
            p = line.split()
            if len(p) == 2: native_events[p[0]] = int(p[1])
            
    # 7. Teardown
    os.rmdir(cg_path)
    time.sleep(0.1)
    bpf.ring_buffer_poll(timeout=100)
    
    # Validation: Do these all join together based purely on immutable IDs?
    has_exec = any(e["type"] == "EXEC" and e["cgroup_id"] == cg_id for e in events)
    has_oom = any(e["type"] == "OOM" and e["cgroup_id"] == cg_id for e in events)
    has_teardown = any(e["type"] == "TEARDOWN" and e["cgroup_id"] == cg_id for e in events)
    valid_join = has_exec and has_oom and has_teardown and (native_events.get("oom_kill", 0) > 0)
    
    receipt = {
        "milestone": "LINUX-CONSOLIDATION-PASS",
        "verdict": "VALID" if valid_join else "INVALID",
        "execution_id": execution_id,
        "chain_of_custody": {
            "identity_binding": {
                "pid": pid,
                "pidfd": pidfd,
                "start_time_jiffies": start_time
            },
            "substrate_envelope": {
                "cgroup_path": cg_path,
                "cgroup_inode": cg_id,
                "memory_limit": "16M"
            },
            "independent_observations": events,
            "native_enforcement": native_events
        }
    }
    
    print(json.dumps(receipt, indent=2))
    with open("consolidation_receipt.json", "w") as f:
        json.dump(receipt, f, indent=2)

if __name__ == "__main__":
    main()
