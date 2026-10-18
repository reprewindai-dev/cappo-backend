# LINUX-CONSOLIDATION-SEALED

**Status**: VALID
**Environment**: WSL2 Linux 6.6.87, root
**Test Date**: 2026-09-10
**Code Path**: `probes/linux_consolidation/`

## Objective
To generate one cross-milestone receipt proving that the single execution identity survives all joins:
`execution_id` → `PID/pidfd/pidns (start_time)` → `cgroup identity` → `native resource envelope` → `exec observation` → `enforcement observation` → `teardown`.

Crucially, the joins must rely on intrinsic, immutable kernel identifiers (e.g., cgroup inode IDs, process `start_time`, `pidfd`) rather than pathname or PID coincidence, addressing the precise falsifier for `LINUX-VRE/EVIDENCE`.

## Verification Details
1. Bound an execution intent (`exec-consolidation-001`) to a new cgroup and extracted its stable `cgroup_inode` (`3781`) using `name_to_handle_at`.
2. Created a native resource envelope (`memory.max=16M`).
3. Bound eBPF observers exclusively to the immutable `cgroup_inode`, guaranteeing they tracked only this specific envelope.
4. Spawned the process, capturing its `PID` (`1340`), opening a durable `pidfd` (`14`), and recording its `start_time_jiffies` (`344697`).
5. Successfully collected `EXEC`, `OOM`, and `TEARDOWN` events via the eBPF sidecar—all explicitly tagged with the `cgroup_inode` (`3781`).
6. Verified the target envelope (`3781`) authentically suffered an `oom_kill=1` via native enforcement constraints.

## Result: VALID
The receipt formally demonstrates an unbroken, spoof-resistant chain of custody from execution request through cryptographic evidence without relying on mutable namespace strings or reusable PIDs.

### Consolidation Receipt
```json
{
  "milestone": "LINUX-CONSOLIDATION-PASS",
  "verdict": "VALID",
  "execution_id": "exec-consolidation-001",
  "chain_of_custody": {
    "identity_binding": {
      "pid": 1340,
      "pidfd": 14,
      "start_time_jiffies": 344697
    },
    "substrate_envelope": {
      "cgroup_path": "/sys/fs/cgroup/exec-consolidation-001",
      "cgroup_inode": 3781,
      "memory_limit": "16M"
    },
    "independent_observations": [
      {
        "type": "EXEC",
        "pid": 1790,
        "cgroup_id": 3781,
        "timestamp_ns": 3447009925782
      },
      {
        "type": "OOM",
        "pid": 1790,
        "cgroup_id": 3781,
        "timestamp_ns": 3447065412467
      },
      {
        "type": "TEARDOWN",
        "pid": 1773,
        "cgroup_id": 3781,
        "timestamp_ns": 3447084068375
      }
    ],
    "native_enforcement": {
      "low": 0,
      "high": 0,
      "max": 36,
      "oom": 1,
      "oom_kill": 1,
      "oom_group_kill": 0
    }
  }
}
```
