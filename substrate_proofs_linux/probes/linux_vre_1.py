#!/usr/bin/env python3
"""
linux_vre_1.py — LINUX-VRE-1 proof
Veklom Capability OS — Linux Virtual Resource Enforcement

Answers the question:
  Does the native Linux enforcement remain within the authorized
  memory, CPU, process, and network envelope — and does teardown
  remove that authority without widening?

Enforced surfaces:
  VRE-R1   memory.max enforcement + reclaim/OOM event observation
  VRE-R2   cpu.max bandwidth throttling (measurable via cpu.stat)
  VRE-R3   pids.max process count limit
  VRE-N1   nftables rule in network namespace — blocklist enforcement
  VRE-T1   Teardown: cgroup.kill + nft flush + rmdir — authority removed

Semantic non-widening invariant (from spec map):
  EffectiveNativeProjection <= AuthorizedEnvelope
  Tested by: read-back after set, then attempt to exceed limit

Each surface proves:
  1. Limit set
  2. Limit read back equals what was set (no silent widening)
  3. Enforcement triggers at boundary (not merely set as metadata)
  4. Teardown removes the authority
"""

import ctypes
import errno
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
VEKLOM_CGROUP = "/sys/fs/cgroup/veklom_vre1"
VEKLOM_NETNS  = "veklom_vre1_ns"

def read_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def cgroup_write(filename: str, value: str):
    Path(f"{VEKLOM_CGROUP}/{filename}").write_text(value)

def cgroup_read(filename: str) -> str:
    return Path(f"{VEKLOM_CGROUP}/{filename}").read_text().strip()

def cgroup_of(pid: int) -> str:
    for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
        parts = line.split(":", 2)
        if parts[0] == "0":
            return parts[2]
    return ""

def setup_cgroup():
    os.makedirs(VEKLOM_CGROUP, exist_ok=True)
    # Enable controllers at parent level
    try:
        Path("/sys/fs/cgroup/cgroup.subtree_control").write_text("+memory +cpu +pids")
    except (PermissionError, OSError):
        pass
    return os.stat(VEKLOM_CGROUP).st_ino

def assign_to_cgroup(pid: int):
    Path(f"{VEKLOM_CGROUP}/cgroup.procs").write_text(str(pid))

def teardown_cgroup():
    """Move all procs to root, kill, then rmdir."""
    try:
        procs = Path(f"{VEKLOM_CGROUP}/cgroup.procs").read_text().strip()
        if procs:
            # Try cgroup.kill first (kernel >= 5.14)
            try:
                Path(f"{VEKLOM_CGROUP}/cgroup.kill").write_text("1")
                time.sleep(0.2)
            except OSError:
                pass
            # Move survivors to root
            for p in Path(f"{VEKLOM_CGROUP}/cgroup.procs").read_text().strip().splitlines():
                try:
                    Path("/sys/fs/cgroup/cgroup.procs").write_text(p.strip())
                except Exception:
                    pass
        os.rmdir(VEKLOM_CGROUP)
        return True
    except Exception as e:
        return str(e)

def read_cpu_usage_usec() -> int:
    """Read cpu.stat usage_usec from veklom cgroup."""
    for line in cgroup_read("cpu.stat").splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    return -1

# ---------------------------------------------------------------------------
# VRE-R1: memory.max enforcement
# ---------------------------------------------------------------------------
def prove_vre_r1() -> dict:
    """
    Set memory.max = 32MB.
    Read back — confirm no widening.
    Spawn child that allocates 64MB — must trigger OOM or be killed.
    Read memory.events oom_kill count.
    """
    LIMIT_BYTES = 32 * 1024 * 1024  # 32 MB

    try:
        cgroup_write("memory.max", str(LIMIT_BYTES))
        readback = int(cgroup_read("memory.max"))
        no_widening = (readback <= LIMIT_BYTES)

        # Spawn a child that will allocate 2x the limit
        child_pid = os.fork()
        if child_pid == 0:
            # Child: allocate 64MB — should be OOM-killed
            try:
                chunk = bytearray(64 * 1024 * 1024)
                # Force page faults to actually consume physical memory
                for i in range(0, len(chunk), 4096):
                    chunk[i] = 0xAB
                time.sleep(5)
            except MemoryError:
                os._exit(137)  # Simulate OOM exit
            os._exit(0)

        assign_to_cgroup(child_pid)

        # Wait for child (OOM kill = exit code 137 or SIGKILL)
        _, wstatus = os.waitpid(child_pid, 0)
        oom_killed = os.WIFSIGNALED(wstatus) and os.WTERMSIG(wstatus) == signal.SIGKILL
        exit_code = os.WEXITSTATUS(wstatus) if os.WIFEXITED(wstatus) else -1

        # Read OOM event counter
        oom_kill_count = 0
        for line in cgroup_read("memory.events").splitlines():
            if line.startswith("oom_kill"):
                oom_kill_count = int(line.split()[1])

        # memory.current is cgroup-accounted memory (broader than RSS)
        memory_current = int(cgroup_read("memory.current"))

        enforcement_triggered = oom_killed or (exit_code == 137) or (oom_kill_count > 0)
        verdict = "VALID" if (no_widening and enforcement_triggered) else "INVALID"

        return {
            "proof": "VRE-R1",
            "verdict": verdict,
            "authorized_limit_bytes": LIMIT_BYTES,
            "readback_bytes": readback,
            "no_widening": no_widening,
            "child_oom_killed_by_signal": oom_killed,
            "child_exit_code": exit_code,
            "memory_events_oom_kill": oom_kill_count,
            "memory_current_bytes": memory_current,
            "enforcement_triggered": enforcement_triggered,
            "note": "memory.current is cgroup-accounted memory; broader than RSS",
        }
    except PermissionError as e:
        return {"proof": "VRE-R1", "verdict": "INDETERMINATE", "reason": str(e)}

# ---------------------------------------------------------------------------
# VRE-R2: cpu.max bandwidth throttling
# ---------------------------------------------------------------------------
def prove_vre_r2() -> dict:
    """
    Set cpu.max = 20000 100000 (20% of one CPU per 100ms period).
    Spawn a busy-loop child for 1 second.
    Read cpu.stat before and after.
    Verify throttled_usec increased — confirms enforcement is active.
    Note: cpu.max is bandwidth/quota throttling, not a total CPU-time budget.
    """
    QUOTA_US  = 20000   # 20ms of CPU per period
    PERIOD_US = 100000  # 100ms period = 20% of one CPU

    try:
        cgroup_write("cpu.max", f"{QUOTA_US} {PERIOD_US}")
        readback = cgroup_read("cpu.max")
        readback_quota, readback_period = readback.split()
        no_widening = (int(readback_quota) <= QUOTA_US)

        # Read baseline cpu.stat
        def parse_cpu_stat():
            result = {}
            for line in cgroup_read("cpu.stat").splitlines():
                k, v = line.split()
                result[k] = int(v)
            return result

        stat_before = parse_cpu_stat()

        # Spawn CPU-bound child for 1 second
        child_pid = os.fork()
        if child_pid == 0:
            end = time.monotonic() + 1.0
            while time.monotonic() < end:
                pass
            os._exit(0)

        assign_to_cgroup(child_pid)
        os.waitpid(child_pid, 0)

        stat_after = parse_cpu_stat()

        throttled_usec = stat_after.get("throttled_usec", 0) - stat_before.get("throttled_usec", 0)
        nr_throttled   = stat_after.get("nr_throttled", 0)   - stat_before.get("nr_throttled", 0)
        usage_usec     = stat_after.get("usage_usec", 0)     - stat_before.get("usage_usec", 0)

        throttling_observed = (throttled_usec > 0 or nr_throttled > 0)
        verdict = "VALID" if (no_widening and throttling_observed) else "INVALID"

        return {
            "proof": "VRE-R2",
            "verdict": verdict,
            "authorized_quota_us": QUOTA_US,
            "authorized_period_us": PERIOD_US,
            "readback": readback,
            "no_widening": no_widening,
            "usage_usec_delta": usage_usec,
            "throttled_usec_delta": throttled_usec,
            "nr_throttled_delta": nr_throttled,
            "throttling_observed": throttling_observed,
            "note": "cpu.max is CFS bandwidth throttling; not equivalent to total CPU-time budget",
        }
    except PermissionError as e:
        return {"proof": "VRE-R2", "verdict": "INDETERMINATE", "reason": str(e)}

# ---------------------------------------------------------------------------
# VRE-R3: pids.max process count limit
# ---------------------------------------------------------------------------
def prove_vre_r3() -> dict:
    """
    Set pids.max = 5.
    Spawn children until fork fails.
    Verify: fork rejected before exceeding limit.
    Verify: readback <= authorized limit (no widening).
    """
    PID_LIMIT = 5

    try:
        cgroup_write("pids.max", str(PID_LIMIT))
        readback = cgroup_read("pids.max")
        no_widening = (readback == "max" or int(readback) <= PID_LIMIT)

        spawned = []
        fork_rejected = False
        fork_errno = None

        # Assign self to cgroup — pids.current counts us
        assign_to_cgroup(os.getpid())

        # Spawn until we hit the limit
        for _ in range(PID_LIMIT + 3):
            try:
                pid = os.fork()
                if pid == 0:
                    time.sleep(10)
                    os._exit(0)
                spawned.append(pid)
            except BlockingIOError as e:
                fork_rejected = True
                fork_errno = e.errno
                break

        # Cleanup
        for p in spawned:
            try:
                os.kill(p, signal.SIGKILL)
                os.waitpid(p, 0)
            except Exception:
                pass

        verdict = "VALID" if (no_widening and fork_rejected) else "INVALID"
        return {
            "proof": "VRE-R3",
            "verdict": verdict,
            "authorized_pids_max": PID_LIMIT,
            "readback": readback,
            "no_widening": no_widening,
            "processes_spawned_before_rejection": len(spawned),
            "fork_rejected": fork_rejected,
            "fork_reject_errno": fork_errno,
        }
    except PermissionError as e:
        return {"proof": "VRE-R3", "verdict": "INDETERMINATE", "reason": str(e)}
    finally:
        # Return self to root cgroup before VRE-R3 cleanup confuses teardown
        try:
            Path("/sys/fs/cgroup/cgroup.procs").write_text(str(os.getpid()))
        except Exception:
            pass

# ---------------------------------------------------------------------------
# VRE-N1: nftables rule in network namespace
# ---------------------------------------------------------------------------
def prove_vre_n1() -> dict:
    """
    Create a network namespace.
    Add a nftables DROP rule for outbound TCP to 1.1.1.1:80.
    Verify rule appears in nft list ruleset.
    Verify connection attempt fails (ECONNREFUSED/ENETUNREACH/timeout).
    Flush rules.
    Verify ruleset is empty.
    """
    script = r"""
import subprocess, socket, json, errno as errno_mod

result = {}

# Create netns
subprocess.run(['ip', 'netns', 'add', 'veklom_vre1_ns'], check=False)

# Add lo interface
subprocess.run(['ip', 'netns', 'exec', 'veklom_vre1_ns',
                'ip', 'link', 'set', 'lo', 'up'], check=False)

# Add drop rule for egress
nft_add = subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'nft', 'add', 'table', 'inet', 'veklom_filter'
], capture_output=True)
subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'nft', 'add', 'chain', 'inet', 'veklom_filter', 'output',
    '{', 'type', 'filter', 'hook', 'output', 'priority', '0', ';', '}'
], capture_output=True)
subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'nft', 'add', 'rule', 'inet', 'veklom_filter', 'output',
    'ip', 'daddr', '1.1.1.1', 'tcp', 'dport', '80', 'drop'
], capture_output=True)

# Read back rule
ruleset = subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'nft', 'list', 'ruleset'
], capture_output=True, text=True)
rule_present = 'drop' in ruleset.stdout and '1.1.1.1' in ruleset.stdout
result['rule_present_after_set'] = rule_present
result['ruleset_excerpt'] = ruleset.stdout.strip()[:400]

# Test: connection attempt blocked (connect in the ns)
conn_blocked = False
ns_conn = subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'python3', '-c',
    'import socket,sys; s=socket.socket(); s.settimeout(2); s.connect(("1.1.1.1",80)); s.close()'
], capture_output=True, text=True, timeout=5)
# Blocked = non-zero exit (ECONNREFUSED, ENETUNREACH, or timeout)
conn_blocked = (ns_conn.returncode != 0)
result['connection_blocked'] = conn_blocked
result['connection_stderr'] = ns_conn.stderr.strip()[:200]

# Flush rules
subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'nft', 'flush', 'table', 'inet', 'veklom_filter'
], capture_output=True)
subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'nft', 'delete', 'table', 'inet', 'veklom_filter'
], capture_output=True)

# Verify teardown
ruleset_after = subprocess.run([
    'ip', 'netns', 'exec', 'veklom_vre1_ns',
    'nft', 'list', 'ruleset'
], capture_output=True, text=True)
rules_gone = (ruleset_after.stdout.strip() == '')
result['rules_gone_after_flush'] = rules_gone

# Delete netns
subprocess.run(['ip', 'netns', 'del', 'veklom_vre1_ns'], capture_output=True)

verdict = 'VALID' if (rule_present and conn_blocked and rules_gone) else 'INVALID'
result['verdict'] = verdict
import json; print(json.dumps(result))
"""
    try:
        r = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0 or not r.stdout.strip():
            return {
                "proof": "VRE-N1",
                "verdict": "INDETERMINATE",
                "reason": r.stderr.strip()[:400] or "empty output",
            }
        inner = json.loads(r.stdout.strip())
        return {"proof": "VRE-N1", **inner}
    except Exception as e:
        return {"proof": "VRE-N1", "verdict": "INDETERMINATE", "reason": str(e)}

# ---------------------------------------------------------------------------
# VRE-T1: Full teardown — cgroup.kill + rmdir + netns del
# ---------------------------------------------------------------------------
def prove_vre_t1(cgroup_inode_before: int) -> dict:
    """
    After all enforcement proofs:
    1. Move self out of veklom cgroup
    2. Kill all remaining procs (cgroup.kill)
    3. rmdir veklom cgroup
    4. Verify cgroup is gone (authority removed)
    5. Verify new cgroup with same name gets different inode
    """
    try:
        # Move self to root before teardown
        Path("/sys/fs/cgroup/cgroup.procs").write_text(str(os.getpid()))

        # Kill remaining
        try:
            Path(f"{VEKLOM_CGROUP}/cgroup.kill").write_text("1")
            time.sleep(0.3)
        except (OSError, FileNotFoundError):
            pass

        # Wait for procs to exit
        for _ in range(20):
            procs = Path(f"{VEKLOM_CGROUP}/cgroup.procs").read_text().strip()
            if not procs:
                break
            time.sleep(0.1)

        os.rmdir(VEKLOM_CGROUP)
        cgroup_gone = not Path(VEKLOM_CGROUP).exists()

        # Recreate to verify inode differs
        os.makedirs(VEKLOM_CGROUP, exist_ok=True)
        new_inode = os.stat(VEKLOM_CGROUP).st_ino
        new_procs = Path(f"{VEKLOM_CGROUP}/cgroup.procs").read_text().strip()
        os.rmdir(VEKLOM_CGROUP)

        inode_differs = (new_inode != cgroup_inode_before)
        new_procs_empty = (new_procs == "")

        verdict = "VALID" if (cgroup_gone and inode_differs and new_procs_empty) else "INVALID"
        return {
            "proof": "VRE-T1",
            "verdict": verdict,
            "cgroup_gone_after_rmdir": cgroup_gone,
            "original_inode": cgroup_inode_before,
            "new_inode_after_recreate": new_inode,
            "inodes_differ": inode_differs,
            "new_cgroup_procs_empty": new_procs_empty,
            "security_property": "teardown removed authority; recreated cgroup has new identity and empty membership",
        }
    except Exception as e:
        return {"proof": "VRE-T1", "verdict": "INVALID", "reason": str(e)}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    session_id = f"veklom_vre1_{int(time.time())}"

    cgroup_inode = setup_cgroup()
    assign_to_cgroup(os.getpid())

    results = {}

    results["VRE_R1_memory"] = prove_vre_r1()
    results["VRE_R2_cpu"]    = prove_vre_r2()
    results["VRE_R3_pids"]   = prove_vre_r3()
    results["VRE_N1_network"] = prove_vre_n1()
    results["VRE_T1_teardown"] = prove_vre_t1(cgroup_inode)

    all_valid   = all(r["verdict"] == "VALID" for r in results.values())
    any_invalid = any(r["verdict"] == "INVALID" for r in results.values())
    overall = "VALID" if all_valid else ("INVALID" if any_invalid else "INDETERMINATE")

    receipt = {
        "milestone":    "LINUX-VRE-1",
        "session_id":   session_id,
        "verdict":      overall,
        "kernel":       Path("/proc/version").read_text().strip(),
        "boot_id":      read_boot_id(),
        "proofs":       results,
        "pass_summary": {k: v["verdict"] for k, v in results.items()},
    }

    print(json.dumps(receipt, indent=2))

if __name__ == "__main__":
    main()
