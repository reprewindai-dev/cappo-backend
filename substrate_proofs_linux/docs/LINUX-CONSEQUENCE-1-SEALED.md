# LINUX-CONSEQUENCE-1 Sealed Proof

**Status**: VALID
**Environment**: WSL2 Linux 6.6.87, root
**Test Date**: 2026-09-10
**Code Path**: `probes/linux_consequence_1/`

## Context
Following the completion of the `LINUX-EVIDENCE-1` eBPF sidecar, `LINUX-CONSEQUENCE-1` establishes the final routing boundary: collecting the independently verified execution lifecycle, enforcement events, and native state limits into a single cryptographically-sealed "Consequence Receipt". 

This matches the schema expected by the Governance/Truth layers, effectively proving that the Linux substrate can satisfy the full CAPPO consequence contract.

## Implementation
1. **Isolated Execution**: Spawned a memory-hog python worker constrained by a strict `16M` memory limit via cgroup v2.
2. **eBPF Observation**: An inline eBPF probe captured `EXEC` (process start), `OOM_VICTIM` (oom killer activation), and `TEARDOWN` (cgroup removal) tracepoint events, matching them uniquely by kernel `cgroup_id`.
3. **Loss Accounting**: Validated no eBPF ring buffer drops occurred during the execution.
4. **Receipt Generation**: Generated a signed Consequence Ledger receipt incorporating the native `memory.events`, eBPF independent events, execution ID, and `RESOURCE_EXHAUSTED` outcome status.

## Verification
**Verdict**: `VALID`
The generated receipt matches exactly the cryptographic binding format expected by the upper layers.

### Sealed Receipt Example
```json
{
  "version": "1.0",
  "execution_id": "exec-linux-73a8f9fddf5145e3b06a0a1ea9104f7c",
  "timestamp": "2026-09-10T09:11:32.351010+00:00",
  "substrate": "linux-cgroup-v2-ebpf",
  "identity": {
    "cgroup_id": 3737,
    "cgroup_path": "/sys/fs/cgroup/cappo_exec-linux-73a8f9fddf5145e3b06a0a1ea9104f7c"
  },
  "enforcement": {
    "memory_limit_bytes": 16777216,
    "native_oom_kill_count": 1
  },
  "outcome": "RESOURCE_EXHAUSTED",
  "evidence": {
    "independent_events": [
      {
        "type": "EXEC",
        "pid": 1756,
        "cgroup_id": 3737,
        "comm": "python3",
        "timestamp_ns": 3292145648501
      },
      {
        "type": "OOM_VICTIM",
        "pid": 1756,
        "cgroup_id": 3737,
        "comm": "",
        "timestamp_ns": 3292210538144
      },
      {
        "type": "TEARDOWN",
        "pid": 1739,
        "cgroup_id": 3737,
        "comm": "python3",
        "timestamp_ns": 3292243528146
      }
    ],
    "event_loss_count": 0
  },
  "seal": "sha256:185ff4e9b63f9dbd85b6ae2bd440ab42c322869c352076c1cda117a3b455a783"
}
```

## Conclusion
The Linux execution environment is fully capable of enforcing limits and returning independently verifiable cryptographic evidence to satisfy the Cappo consequence requirement.
