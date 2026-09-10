import sys
import os
import time
import json
import uuid
import ctypes
import traceback
from bcc import BPF

BPF_CODE_PATH = os.path.join(os.path.dirname(__file__), "linux_evidence_lifecycle.bpf.c")

def setup_cgroup(name):
    cg_path = f"/sys/fs/cgroup/{name}"
    os.makedirs(cg_path, exist_ok=True)
    
    # Get cgroup ID via name_to_handle_at
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

def run_test(mode):
    with open(BPF_CODE_PATH, "r") as f:
        bpf_text = f.read()
        
    b = BPF(text=bpf_text)
    
    cg_name = f"e1_lifecycle_{mode}_{uuid.uuid4().hex[:8]}"
    cg_path, cg_id = setup_cgroup(cg_name)
    
    b["config"][0] = ctypes.c_uint64(cg_id)
    
    events_captured = []
    def handle_event(cpu, data, size):
        event = b["events"].event(data)
        events_captured.append({
            "event_type": event.event_type,
            "pid": event.pid,
            "cgroup_id": event.cgroup_id,
            "comm": event.comm.decode('utf-8', 'replace')
        })
        
    b["events"].open_ring_buffer(handle_event)
    
    target_pid = -1
    
    if mode == "E1-C":
        # Just run a quick command and then remove the cgroup
        gen_pid = os.fork()
        if gen_pid == 0:
            with open(os.path.join(cg_path, "cgroup.procs"), "w") as f:
                f.write(str(os.getpid()))
            # This triggers sched_process_exec
            os.execlp("true", "true")
            os._exit(0)
            
        target_pid = gen_pid
        os.waitpid(gen_pid, 0)
        
        # Remove the cgroup to trigger cgroup_rmdir
        teardown_cgroup(cg_path)
        
        # Drain events
        time.sleep(0.1)
        b.ring_buffer_poll(timeout=100)
        
        seen = b["stats"][0].value
        submitted = b["stats"][1].value
        drops = b["stats"][2].value
        delivered = len(events_captured)
        
        has_exec = any(e["event_type"] == 3 and e["cgroup_id"] == cg_id for e in events_captured)
        has_rmdir = any(e["event_type"] == 1 and e["cgroup_id"] == cg_id for e in events_captured)
        
        verdict = "VALID" if (has_exec and has_rmdir and seen == submitted + drops and delivered == submitted) else "INVALID"
        
        return {
            "proof": "E1-C",
            "verdict": verdict,
            "target_cgroup_id": cg_id,
            "has_exec": has_exec,
            "has_rmdir": has_rmdir,
            "transport_metrics": {
                "seen_total": seen,
                "submitted_total": submitted,
                "reserve_failures": drops,
                "delivered_total": delivered
            }
        }
        
    elif mode == "E1-D":
        # OOM correlation
        with open(os.path.join(cg_path, "memory.max"), "w") as f:
            f.write("16M")
            
        # Spawn memory hog
        gen_pid = os.fork()
        if gen_pid == 0:
            with open(os.path.join(cg_path, "cgroup.procs"), "w") as f:
                f.write(str(os.getpid()))
            # allocate 32MB string, exceeds 16M
            os.execlp("python3", "python3", "-c", "a = 'x' * (32 * 1024 * 1024); import time; time.sleep(10)")
            os._exit(0)
            
        target_pid = gen_pid
        pid, status = os.waitpid(gen_pid, 0)
        
        # Check native cgroup stats
        mem_events = read_memory_events(cg_path)
        native_oom_kill = mem_events.get("oom_kill", 0)
        
        # Drain events
        time.sleep(0.1)
        b.ring_buffer_poll(timeout=100)
        
        seen = b["stats"][0].value
        submitted = b["stats"][1].value
        drops = b["stats"][2].value
        delivered = len(events_captured)
        
        # We look for a mark_victim event (event_type == 2) in our target cgroup
        has_mark_victim = any(e["event_type"] == 2 and e["cgroup_id"] == cg_id for e in events_captured)
        has_exec = any(e["event_type"] == 3 and e["cgroup_id"] == cg_id for e in events_captured)
        
        teardown_cgroup(cg_path)
        
        verdict = "VALID" if (has_mark_victim and native_oom_kill > 0 and seen == submitted + drops and delivered == submitted) else "INVALID"
        
        if verdict == "INVALID":
            print("Captured events:", events_captured)

        return {
            "proof": "E1-D",
            "verdict": verdict,
            "target_cgroup_id": cg_id,
            "target_pid": target_pid,
            "native_oom_kill": native_oom_kill,
            "ebpf_mark_victim_seen": has_mark_victim,
            "exit_status": status,
            "transport_metrics": {
                "seen_total": seen,
                "submitted_total": submitted,
                "reserve_failures": drops,
                "delivered_total": delivered
            }
        }
        
def main():
    try:
        res_c = run_test("E1-C")
        print(json.dumps(res_c, indent=2))
        sys.stdout.flush()
    except Exception as e:
        print(f"E1-C Exception: {e}")
        traceback.print_exc()

    try:
        res_d = run_test("E1-D")
        print(json.dumps(res_d, indent=2))
        sys.stdout.flush()
    except Exception as e:
        print(f"E1-D Exception: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
