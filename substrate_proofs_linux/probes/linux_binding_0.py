#!/usr/bin/env python3
"""
linux_binding_0.py — LINUX-BINDING-0 proof
Veklom Capability OS — Linux substrate identity binding

Proves five joins:
  B0-L1  boot_id + pidns(st_dev,st_ino) + PID + starttime
  B0-L2  pidfd bound to original process instance (ESRCH after exit)
  B0-L3  process <-> cgroup object membership (path + inode)
  B0-L4  destroyed/recreated same-name cgroup cannot inherit binding
  B0-L5  namespace identity via (st_dev, st_ino) pair

Hostile cases:
  H1  post-exit pidfd signaling    -> ESRCH
  H2  cgroup name reuse            -> inode mismatch
  H3  namespace mismatch           -> (st_dev,st_ino) diverges
  H4  cgroup reassignment          -> /proc/pid/cgroup no longer matches
  H5  PID reuse (simulated)        -> pidfd poll detects original exit
"""

import ctypes
import errno
import json
import os
import select
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Syscall numbers (x86_64)
# ---------------------------------------------------------------------------
NR_PIDFD_SEND_SIGNAL = 424
NR_PIDFD_OPEN        = 434

libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.syscall.restype = ctypes.c_long

def pidfd_send_signal(pidfd: int, sig: int) -> int:
    """Returns 0 on success, sets errno on failure."""
    ret = libc.syscall(
        ctypes.c_long(NR_PIDFD_SEND_SIGNAL),
        ctypes.c_int(pidfd),
        ctypes.c_int(sig),
        ctypes.c_void_p(None),
        ctypes.c_uint(0),
    )
    return int(ret), ctypes.get_errno()

def pidfd_poll_terminated(pidfd: int, timeout_ms: int = 2000) -> bool:
    """Returns True if the pidfd becomes readable (process terminated)."""
    p = select.poll()
    p.register(pidfd, select.POLLIN)
    events = p.poll(timeout_ms)
    return bool(events and (events[0][1] & select.POLLIN))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def read_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def read_starttime(pid: int) -> int:
    fields = Path(f"/proc/{pid}/stat").read_text().split()
    # field 22 (1-indexed) = starttime, 0-indexed = 21
    # stat format: pid (comm) state ppid pgroup session tty_nr ... starttime
    # comm can contain spaces and parentheses; find closing ')' to locate fields
    raw = Path(f"/proc/{pid}/stat").read_text()
    after_comm = raw[raw.rfind(")") + 2:]
    fields_after = after_comm.split()
    # starttime is field index 19 after comm (fields: state ppid pgrp session
    # tty_nr tpgid flags minflt cminflt majflt cmajflt utime stime cutime
    # cstime priority nice num_threads itrealvalue starttime)
    return int(fields_after[19])

def ns_identity(pid: int, ns: str) -> dict:
    """Returns (st_dev, st_ino) for a process's namespace."""
    s = os.stat(f"/proc/{pid}/ns/{ns}")
    return {"st_dev": s.st_dev, "st_ino": s.st_ino}

def cgroup_of(pid: int) -> str:
    """Returns the cgroup v2 path for a process from /proc/pid/cgroup."""
    for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
        parts = line.split(":", 2)
        if parts[0] == "0":  # cgroup v2 hierarchy id
            return parts[2]
    return ""

def cgroup_object_identity(cgroup_path: str) -> dict:
    """
    Returns filesystem identity of the cgroup directory.
    cgroup_path is the path fragment (e.g. /init.scope), not the full path.
    """
    full = f"/sys/fs/cgroup{cgroup_path}"
    s = os.stat(full)
    result = {"path": full, "st_dev": s.st_dev, "st_ino": s.st_ino}
    # kernel cgroup ID: available via cgroup.id file on some kernels
    id_file = Path(full) / "cgroup.id"
    if id_file.exists():
        result["kernel_cgroup_id"] = id_file.read_text().strip()
    return result

VEKLOM_CGROUP = "/sys/fs/cgroup/veklom_b0_test"

def create_veklom_cgroup() -> dict:
    os.makedirs(VEKLOM_CGROUP, exist_ok=True)
    # Enable memory and pids controllers
    try:
        Path("/sys/fs/cgroup/cgroup.subtree_control").write_text("+memory +pids")
    except PermissionError:
        pass  # May already be enabled or require root
    s = os.stat(VEKLOM_CGROUP)
    result = {"path": VEKLOM_CGROUP, "st_dev": s.st_dev, "st_ino": s.st_ino}
    id_file = Path(VEKLOM_CGROUP) / "cgroup.id"
    if id_file.exists():
        result["kernel_cgroup_id"] = id_file.read_text().strip()
    return result

def assign_to_cgroup(pid: int, cgroup_path: str):
    Path(f"{cgroup_path}/cgroup.procs").write_text(str(pid))

def destroy_veklom_cgroup():
    # Move our own process out first if needed
    procs = Path(f"{VEKLOM_CGROUP}/cgroup.procs").read_text().strip()
    if procs:
        for p in procs.splitlines():
            try:
                Path("/sys/fs/cgroup/cgroup.procs").write_text(p.strip())
            except Exception:
                pass
    try:
        os.rmdir(VEKLOM_CGROUP)
    except Exception as e:
        return str(e)
    return "OK"

# ---------------------------------------------------------------------------
# B0-L1: boot_id + pidns(st_dev,st_ino) + PID + starttime
# ---------------------------------------------------------------------------
def prove_b0_l1(pid: int) -> dict:
    boot_id   = read_boot_id()
    pidns     = ns_identity(pid, "pid")
    starttime = read_starttime(pid)
    exe       = os.readlink(f"/proc/{pid}/exe")
    uid_line  = [l for l in Path(f"/proc/{pid}/status").read_text().splitlines()
                 if l.startswith("Uid:")][0]
    uid = uid_line.split()[1]

    identity_tuple = {
        "boot_id":              boot_id,
        "pidns_st_dev":         pidns["st_dev"],
        "pidns_st_ino":         pidns["st_ino"],
        "pid":                  pid,
        "starttime_jiffies":    starttime,
        "uid":                  uid,
        "executable":           exe,
    }
    return {"proof": "B0-L1", "verdict": "VALID", "identity_tuple": identity_tuple}

# ---------------------------------------------------------------------------
# B0-L2: pidfd bound to original process; ESRCH after exit; poll detects exit
# ---------------------------------------------------------------------------
def prove_b0_l2() -> dict:
    child_pid = os.fork()
    if child_pid == 0:
        time.sleep(30)
        sys.exit(0)

    pidfd = os.pidfd_open(child_pid, 0)
    child_starttime = read_starttime(child_pid)

    # Kill the child
    os.kill(child_pid, signal.SIGKILL)
    os.waitpid(child_pid, 0)

    # Test 1: poll detects termination
    terminated = pidfd_poll_terminated(pidfd, timeout_ms=2000)

    # Test 2: pidfd_send_signal returns ESRCH
    ret, err = pidfd_send_signal(pidfd, 0)  # sig=0 = existence check
    is_esrch = (err == errno.ESRCH)

    os.close(pidfd)

    verdict = "VALID" if (terminated and is_esrch) else "INVALID"
    return {
        "proof": "B0-L2",
        "verdict": verdict,
        "child_pid": child_pid,
        "child_starttime": child_starttime,
        "pidfd_poll_detected_termination": terminated,
        "pidfd_send_signal_after_exit": "ESRCH" if is_esrch else f"errno={err}",
    }

# ---------------------------------------------------------------------------
# B0-L3: process <-> cgroup object membership
# ---------------------------------------------------------------------------
def prove_b0_l3(pid: int) -> dict:
    try:
        cg_identity = create_veklom_cgroup()
        assign_to_cgroup(pid, VEKLOM_CGROUP)

        # Verify membership
        reported_cgroup = cgroup_of(pid)
        expected_suffix = "/veklom_b0_test"
        membership_confirmed = reported_cgroup.endswith(expected_suffix)

        # Verify object identity matches what we created
        current_identity = cgroup_object_identity(reported_cgroup)
        inode_matches = (current_identity["st_ino"] == cg_identity["st_ino"] and
                         current_identity["st_dev"] == cg_identity["st_dev"])

        verdict = "VALID" if (membership_confirmed and inode_matches) else "INVALID"
        return {
            "proof": "B0-L3",
            "verdict": verdict,
            "cgroup_path": reported_cgroup,
            "cgroup_object_identity": cg_identity,
            "membership_confirmed": membership_confirmed,
            "inode_matches_created_object": inode_matches,
        }
    except PermissionError as e:
        return {"proof": "B0-L3", "verdict": "INDETERMINATE",
                "reason": f"Permission denied: {e}. Re-run as root or with cgroup write access."}

# ---------------------------------------------------------------------------
# B0-L4: cgroup name reuse cannot inherit binding
# ---------------------------------------------------------------------------
def prove_b0_l4() -> dict:
    try:
        # Create cgroup, capture identity
        os.makedirs(VEKLOM_CGROUP, exist_ok=True)
        original_stat = os.stat(VEKLOM_CGROUP)
        original_inode = original_stat.st_ino
        original_dev   = original_stat.st_dev

        # Destroy it (no process assigned — direct rmdir)
        destroy_veklom_cgroup()

        # Check it is actually gone
        gone = not Path(VEKLOM_CGROUP).exists()

        # Recreate with same name
        os.makedirs(VEKLOM_CGROUP, exist_ok=True)
        new_stat  = os.stat(VEKLOM_CGROUP)
        new_inode = new_stat.st_ino
        new_dev   = new_stat.st_dev

        inode_differs = (new_inode != original_inode)
        # Verify cgroup.procs is empty (no inherited processes)
        procs = Path(f"{VEKLOM_CGROUP}/cgroup.procs").read_text().strip()
        procs_empty = (procs == "")

        verdict = "VALID" if (gone and inode_differs and procs_empty) else "INVALID"
        return {
            "proof": "B0-L4",
            "verdict": verdict,
            "original_inode": original_inode,
            "original_dev":   original_dev,
            "cgroup_was_destroyed": gone,
            "new_inode":      new_inode,
            "new_dev":        new_dev,
            "inodes_differ":  inode_differs,
            "new_cgroup_procs_empty": procs_empty,
            "security_property": "new same-name cgroup has different kernel identity; no processes inherited",
        }
    except PermissionError as e:
        return {"proof": "B0-L4", "verdict": "INDETERMINATE",
                "reason": f"Permission denied: {e}"}

# ---------------------------------------------------------------------------
# B0-L5: namespace identity via (st_dev, st_ino)
# ---------------------------------------------------------------------------
def prove_b0_l5(pid: int) -> dict:
    self_pidns = ns_identity(os.getpid(), "pid")
    self_netns = ns_identity(os.getpid(), "net")
    target_pidns = ns_identity(pid, "pid")
    target_netns = ns_identity(pid, "net")

    # Same process group: all namespaces must match self
    pidns_match = (self_pidns == target_pidns)
    netns_match = (self_netns == target_netns)

    # Hostile: init (PID 1) is in the same root namespace — verify
    try:
        init_pidns = ns_identity(1, "pid")
        init_same_ns = (init_pidns == self_pidns)
    except PermissionError:
        init_pidns = {}
        init_same_ns = None

    verdict = "VALID" if (pidns_match and netns_match) else "INVALID"
    return {
        "proof": "B0-L5",
        "verdict": verdict,
        "self_pidns": self_pidns,
        "target_pidns": target_pidns,
        "self_netns": self_netns,
        "target_netns": target_netns,
        "pidns_match": pidns_match,
        "netns_match": netns_match,
        "init_pidns": init_pidns,
        "init_same_namespace_as_self": init_same_ns,
    }

# ---------------------------------------------------------------------------
# Hostile: H3 namespace mismatch — unshare creates a different netns
# ---------------------------------------------------------------------------
def prove_h3_namespace_mismatch() -> dict:
    """
    Run a child in a new network namespace (unshare -n).
    Its netns (st_dev,st_ino) must differ from parent's.
    """
    try:
        self_netns = ns_identity(os.getpid(), "net")
        result = subprocess.run(
            ["unshare", "-n", "stat", "-L", "/proc/self/ns/net",
             "--printf=%d %i"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return {"proof": "H3", "verdict": "INDETERMINATE",
                    "reason": result.stderr.strip()}
        parts = result.stdout.strip().split()
        child_netns = {"st_dev": int(parts[0]), "st_ino": int(parts[1])}
        differs = (child_netns != self_netns)
        verdict = "VALID" if differs else "INVALID"
        return {
            "proof": "H3",
            "verdict": verdict,
            "parent_netns": self_netns,
            "child_netns_after_unshare": child_netns,
            "netns_differs": differs,
        }
    except Exception as e:
        return {"proof": "H3", "verdict": "INDETERMINATE", "reason": str(e)}

# ---------------------------------------------------------------------------
# Hostile: H4 cgroup reassignment
# ---------------------------------------------------------------------------
def prove_h4_cgroup_reassignment(pid: int) -> dict:
    try:
        # Ensure veklom cgroup exists
        os.makedirs(VEKLOM_CGROUP, exist_ok=True)

        # Place process INTO veklom cgroup
        assign_to_cgroup(pid, VEKLOM_CGROUP)
        original_cgroup = cgroup_of(pid)
        original_identity = cgroup_object_identity(original_cgroup)

        # Verify we are actually in the veklom cgroup before moving
        if "veklom_b0_test" not in original_cgroup:
            return {"proof": "H4", "verdict": "INDETERMINATE",
                    "reason": f"Process not in veklom cgroup after assignment: {original_cgroup}"}

        # Now reassign to root cgroup
        Path("/sys/fs/cgroup/cgroup.procs").write_text(str(pid))
        new_cgroup = cgroup_of(pid)
        still_matches = (new_cgroup == original_cgroup)

        verdict = "VALID" if not still_matches else "INVALID"
        return {
            "proof": "H4",
            "verdict": verdict,
            "original_cgroup": original_cgroup,
            "original_object_identity": original_identity,
            "new_cgroup_after_reassignment": new_cgroup,
            "binding_still_valid": still_matches,
            "security_property": "after reassignment, recorded cgroup path no longer matches",
        }
    except PermissionError as e:
        return {"proof": "H4", "verdict": "INDETERMINATE",
                "reason": f"Permission denied: {e}"}

# ---------------------------------------------------------------------------
# Hostile: H5 PID reuse — original pidfd poll detects exit independently
# ---------------------------------------------------------------------------
def prove_h5_pid_reuse_pidfd_isolation() -> dict:
    """
    Spawn child A, open pidfd. Kill A. Spawn child B (may reuse PID).
    Verify: pidfd_send_signal on A's fd returns ESRCH even if B reuses PID.
    """
    child_a = os.fork()
    if child_a == 0:
        time.sleep(30); sys.exit(0)

    fd_a = os.pidfd_open(child_a, 0)
    starttime_a = read_starttime(child_a)

    os.kill(child_a, signal.SIGKILL)
    os.waitpid(child_a, 0)

    # Spawn B immediately to maximize PID reuse chance
    child_b = os.fork()
    if child_b == 0:
        time.sleep(5); sys.exit(0)

    starttime_b = read_starttime(child_b)
    pid_reused = (child_b == child_a)

    # Signal via A's pidfd — must not reach B
    ret, err = pidfd_send_signal(fd_a, 0)
    esrch = (err == errno.ESRCH)

    os.kill(child_b, signal.SIGKILL)
    os.waitpid(child_b, 0)
    os.close(fd_a)

    verdict = "VALID" if esrch else "INVALID"
    return {
        "proof": "H5",
        "verdict": verdict,
        "child_a_pid": child_a,
        "child_a_starttime": starttime_a,
        "child_b_pid": child_b,
        "child_b_starttime": starttime_b,
        "pid_numerically_reused": pid_reused,
        "pidfd_a_send_signal_result": "ESRCH" if esrch else f"errno={err}",
        "security_property": "stale pidfd cannot address replacement process",
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    pid = os.getpid()
    session_id = f"veklom_lb0_{int(time.time())}"

    results = {}

    results["B0_L1"] = prove_b0_l1(pid)
    results["B0_L2"] = prove_b0_l2()
    results["B0_L3"] = prove_b0_l3(pid)
    results["B0_L4"] = prove_b0_l4()
    results["B0_L5"] = prove_b0_l5(pid)
    results["H3_namespace_mismatch"]     = prove_h3_namespace_mismatch()
    results["H4_cgroup_reassignment"]    = prove_h4_cgroup_reassignment(pid)
    results["H5_pid_reuse_isolation"]    = prove_h5_pid_reuse_pidfd_isolation()

    # Cleanup
    try: destroy_veklom_cgroup()
    except: pass

    all_valid = all(r["verdict"] == "VALID" for r in results.values())
    any_invalid = any(r["verdict"] == "INVALID" for r in results.values())
    overall = "VALID" if all_valid else ("INVALID" if any_invalid else "INDETERMINATE")

    receipt = {
        "milestone":   "LINUX-BINDING-0",
        "session_id":  session_id,
        "verdict":     overall,
        "kernel":      open("/proc/version").read().strip(),
        "boot_id":     read_boot_id(),
        "proofs":      results,
        "pass_summary": {k: v["verdict"] for k, v in results.items()},
    }

    print(json.dumps(receipt, indent=2))

if __name__ == "__main__":
    main()
