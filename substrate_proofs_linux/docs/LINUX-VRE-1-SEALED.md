# LINUX-VRE-1 — SEALED LOCAL PROOF

**Sealed:** 2026-09-10  
**Status:** VALID  
**Scope:** Local WSL2 substrate, elevated/root execution

---

## Result

```
LINUX-VRE-1: VALID
All 5 enforcement proofs: VALID
```

---

## Provenance

| Field | Value |
|---|---|
| session_id | veklom_vre1_1789012111 (Core) / veklom_n1_causal (Network) |
| boot_id | 0b063e21-0207-4fb2-9080-812612fc2a89 |
| kernel | 6.6.87.2-microsoft-standard-WSL2 |
| Python | 3.14.4 |
| Distribution | Ubuntu 26.04 LTS |
| Architecture | x86_64 |
| Privilege | root (elevated) |
| Execution timestamp | 2026-09-10T04:04:57Z |
| probe_linux_vre_1_sha256 | 022227c52a79bd05c095b37dc3049cc108e38351934d9957f07f01bd328f5355 |
| probe_vren1_causal_sha256 | 63e55fd10560ffd6bc22aba6cb5bff155b8c7bfc0e761b62714a93af4da4604c |

---

## What Was Proven

**VRE-R1 — `memory.max` enforcement**  
*   **Set:** `memory.max` configured at 32 MiB.
*   **Readback:** `33554432` bytes (no silent widening).
*   **Consequence:** Hostile process killed by `SIGKILL` (-9) upon exceeding limit.
*   **Event observation:** `memory.events` recorded `oom_kill 1`.

**VRE-R2 — `cpu.max` bandwidth restriction**  
*   **Set:** `cpu.max` configured at `20000 100000` (20% of one CPU per 100ms period).
*   **Readback:** `20000 100000` (no silent widening).
*   **Consequence:** Kernel scheduler enforced CPU bandwidth restriction. During ~1s of busy-loop wallclock time, process was limited to ~217.8 ms CPU usage (`usage_usec_delta`).
*   **Event observation:** `cpu.stat` recorded ~799.2 ms of active throttling (`throttled_usec_delta`) across 10 distinct periods (`nr_throttled_delta`).

**VRE-R3 — `pids.max` process count limit**  
*   **Set:** `pids.max` configured at `5`.
*   **Readback:** `5` (no silent widening).
*   **Consequence:** After parent + 4 children reached `pids.current = 5`, the 6th fork attempt was successfully rejected by the kernel with `EAGAIN` (errno 11).

**VRE-N1 — `nftables` causal enforcement (Causal Sandwich)**  
*   **1. Control:** Connection to 127.0.0.1:9090 inside `veklom_n1_causal` netns succeeded (`OK`).
*   **2. Enforce:** `drop` rule installed in `veklom_filter` output chain.
*   **3. Readback:** Rule confirmed present in `nft list ruleset`.
*   **4. Consequence:** Same connection attempt timed out (`BLOCKED:TimeoutError`).
*   **5. Teardown:** Table flushed and deleted.
*   **6. Recovery:** Same connection attempt succeeded again (`OK`).
*   The causal link between the native kernel object and the consequence is strictly proven.

**VRE-T1 — Teardown removes authority**  
*   **Kill:** `cgroup.kill` executed successfully.
*   **Remove:** `rmdir` executed, `cgroup_gone_after_rmdir = true`.
*   **Recreate:** Recreated cgroup received a new numeric inode (`2683` → `2727`) and had an empty `cgroup.procs` membership.
*   *Note: As established in LINUX-BINDING-0, the proof is fresh rebinding, not a promise that numeric inode values can never eventually be reused.*

---

## Conclusion

On the measured local WSL2/Linux substrate, Veklom demonstrated native kernel enforcement of memory, CPU bandwidth, process-count, and network boundaries, with enforced-state readback and teardown, without relying solely on configuration metadata.

---

## Program State After This Seal

```
LINUX-SPEC-MAP-0    ✅ SEALED
LINUX-BINDING-0     ✅ SEALED / VALID
LINUX-VRE-1         ✅ SEALED / VALID
LINUX-EVIDENCE-1    🟢 GO
```
