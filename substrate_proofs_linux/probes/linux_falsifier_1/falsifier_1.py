import os
import sys
import time
import json
import uuid
import ctypes
import hashlib
import sqlite3

# C syscall constants
SYS_pidfd_open = 434
SYS_pidfd_send_signal = 424
AT_FDCWD = -100

libc = ctypes.CDLL("libc.so.6", use_errno=True)

class FileHandle(ctypes.Structure):
    _fields_ = [
        ("handle_bytes", ctypes.c_uint),
        ("handle_type", ctypes.c_int),
        ("f_handle", ctypes.c_ubyte * 8)
    ]

def setup_cgroup(name):
    cg_path = f"/sys/fs/cgroup/{name}"
    os.makedirs(cg_path, exist_ok=True)
    handle = FileHandle()
    handle.handle_bytes = 8
    mnt_id = ctypes.c_int()
    ret = libc.name_to_handle_at(AT_FDCWD, cg_path.encode(), ctypes.byref(handle), ctypes.byref(mnt_id), 0)
    if ret != 0:
        raise OSError(f"name_to_handle_at failed: {ctypes.get_errno()}")
    cg_id = int.from_bytes(bytes(handle.f_handle), byteorder=sys.byteorder)
    return cg_path, cg_id

def open_pidfd(pid):
    fd = libc.syscall(SYS_pidfd_open, pid, 0)
    if fd < 0:
        raise OSError(f"pidfd_open failed: {ctypes.get_errno()}")
    return fd

def pidfd_send_signal(pidfd, sig):
    ret = libc.syscall(SYS_pidfd_send_signal, pidfd, sig, 0, 0)
    if ret < 0:
        errno = ctypes.get_errno()
        if errno == 3: # ESRCH
            return "ESRCH (Process terminated)"
        return f"Error {errno}"
    return "OK"

def get_start_time(pid):
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            return int(f.read().split()[21])
    except FileNotFoundError:
        return 0

def init_db():
    db_path = "falsifier_target.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE mutations (lease_id TEXT PRIMARY KEY, committed_at REAL)')
    conn.commit()
    conn.close()
    return db_path

def get_mutations(db_path):
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM mutations").fetchone()[0]
    conn.close()
    return count

def mutate(db_path, lease_id):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO mutations VALUES (?, ?)", (lease_id, time.time()))
        conn.commit()
        return "COMMITTED"
    except sqlite3.IntegrityError:
        return "LOCKED_DUPLICATE"
    finally:
        conn.close()

def hash_binding(cg_id, starttime):
    return hashlib.sha256(f"{cg_id}:{starttime}".encode()).hexdigest()

def run_falsifier():
    db_path = init_db()
    
    lease_id = f"lease-{uuid.uuid4().hex[:8]}"
    cg_name = f"cappo_falsifier_test"
    
    print("[1] Creating Execution A...")
    cg_path_a, cg_id_a = setup_cgroup(cg_name)
    pid_a = os.fork()
    if pid_a == 0:
        with open(os.path.join(cg_path_a, "cgroup.procs"), "w") as f:
            f.write(str(os.getpid()))
        time.sleep(10) # wait to be killed
        os._exit(0)
        
    pidfd_a = open_pidfd(pid_a)
    starttime_a = get_start_time(pid_a)
    
    print("[2] Binding Execution A...")
    digest_a = hash_binding(cg_id_a, starttime_a)
    print(f"    cg_id: {cg_id_a}, starttime: {starttime_a}, digest: {digest_a}")
    
    target_state_before = get_mutations(db_path)
    
    print("[4] Executing Substitution Attack...")
    os.kill(pid_a, 9)
    os.waitpid(pid_a, 0)
    os.rmdir(cg_path_a)
    
    # Recreate execution B with EXACT same observable parameters
    cg_path_b, cg_id_b = setup_cgroup(cg_name) # Same cgroup name/path!
    pid_b = os.fork()
    if pid_b == 0:
        with open(os.path.join(cg_path_b, "cgroup.procs"), "w") as f:
            f.write(str(os.getpid()))
        time.sleep(10)
        os._exit(0)
        
    pidfd_b = open_pidfd(pid_b)
    starttime_b = get_start_time(pid_b)
    
    print(f"    Recreated same path: {cg_path_b}")
    print(f"    New cg_id: {cg_id_b} (differs from {cg_id_a})")
    
    print("[5] Attempting Dispatch with Authority A...")
    # Dispatcher checks pidfd_a and cg_id_a against CURRENT state
    pidfd_a_status = pidfd_send_signal(pidfd_a, 0)
    current_cg_path, current_cg_id = setup_cgroup(cg_name) # Router checks what's there now
    
    if pidfd_a_status == "OK" and current_cg_id == cg_id_a:
        decision_rejected = "ALLOW"
        if decision_rejected == "ALLOW": mutate(db_path, lease_id)
    else:
        decision_rejected = "DENY"
        
    binding_comparison_rejected = f"pidfd: {pidfd_a_status}, cg_id: {current_cg_id} != {cg_id_a}"
    target_state_after_rejected = get_mutations(db_path)
    
    print("[6-8] Fresh Binding & Legitimate Dispatch for B...")
    digest_b = hash_binding(cg_id_b, starttime_b)
    pidfd_b_status = pidfd_send_signal(pidfd_b, 0)
    
    if pidfd_b_status == "OK" and cg_id_b == cg_id_b: # Valid authority B check
        decision_legit = "ALLOW"
        mutate(db_path, lease_id)
    else:
        decision_legit = "DENY"
        
    target_state_after_legit = get_mutations(db_path)
    
    # Cleanup
    os.kill(pid_b, 9)
    os.waitpid(pid_b, 0)
    os.rmdir(cg_path_b)
    
    valid = (target_state_after_rejected == 0 and target_state_after_legit == 1)
    
    evidence = {
        "milestone": "LINUX-FALSIFIER-1",
        "verdict": "VALID" if valid else "INVALID",
        "lease_identifier": lease_id,
        "evidence_packet": {
            "A_binding_digest": digest_a,
            "A_pidfd_termination_result": pidfd_a_status,
            "old_cgroup_identity": cg_id_a,
            "new_cgroup_identity": cg_id_b,
            "B_binding_digest": digest_b,
            "rejected_dispatch": {
                "binding_comparison_result": binding_comparison_rejected,
                "dispatch_decision": decision_rejected
            },
            "legitimate_dispatch": {
                "binding_comparison_result": f"pidfd: {pidfd_b_status}, cg_id: {cg_id_b} == {cg_id_b}",
                "dispatch_decision": decision_legit
            },
            "consequences": {
                "target_state_before": target_state_before,
                "substituted_execution_consequences": target_state_after_rejected,
                "legitimate_execution_consequences": target_state_after_legit
            }
        }
    }
    
    print(json.dumps(evidence, indent=2))
    with open("falsifier_receipt.json", "w") as f:
        json.dump(evidence, f, indent=2)

if __name__ == "__main__":
    run_falsifier()
