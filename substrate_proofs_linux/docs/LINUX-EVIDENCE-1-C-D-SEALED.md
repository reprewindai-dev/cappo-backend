# LINUX-EVIDENCE-1 E1-C and E1-D Sealed Proof

**Status**: VALID
**Environment**: WSL2 Linux 6.6.87, root
**Test Date**: 2026-09-10
**Code Path**: `probes/linux_evidence_lifecycle/`

## Objective
To extend the `LINUX-EVIDENCE-1` eBPF sidecar verification to definitively cover execution lifecycle (E1-C) and native enforcement (E1-D) correlation, while explicitly preserving the closed-accounting transport proofs established in E1-A/E1-B (`seen_total = submitted_total + reserve_failures`).

## E1-C: Lifecycle Correlation
**Methodology**:
1. Dynamically instrumented `tracepoint:sched:sched_process_exec` and `tracepoint:cgroup:cgroup_rmdir`.
2. Spawned a worker bound to a specific cgroup.
3. Teardown the cgroup and matched all events via `cgroup_id`.
4. Read back the transport accounting counters from the eBPF maps.

**Result**: `VALID`
*   `has_exec`: `true`
*   `has_rmdir`: `true`
*   `seen_total`: `2`
*   `submitted_total`: `2`
*   `reserve_failures`: `0`
*   `delivered_total`: `2`

The independent eBPF observer flawlessly captured the substrate creation and destruction without dropping any events in transport. 

## E1-D: Enforcement Correlation (OOM)
**Methodology**:
1. Constrained a cgroup with `memory.max=16M`.
2. Spawned a hostile python worker attempting to allocate 32MB.
3. Instrumented `tracepoint:oom:mark_victim` and explicitly correlated via `cgroup_id`.
4. Extracted native `memory.events oom_kill` delta and verified worker SIGKILL status.
5. Read back the transport accounting counters from the eBPF maps.

**Result**: `VALID`
*   `native_oom_kill`: `1`
*   `ebpf_mark_victim_seen`: `true`
*   `exit_status`: `9`
*   `seen_total`: `2`
*   `submitted_total`: `2`
*   `reserve_failures`: `0`
*   `delivered_total`: `2`

This uniquely binds the native kernel limit enforcement (`memory.max`), native telemetry (`memory.events`), independent observation (`mark_victim`), and final process consequence (`SIGKILL`) together with zero eBPF event loss.

## Conclusion
The full `LINUX-EVIDENCE-1` boundary is now proven across E1-A, E1-B, E1-C, and E1-D. The accounting guarantees proven in E1-A/B securely carry forward into the real lifecycle and enforcement tracepoints of E1-C/D.
