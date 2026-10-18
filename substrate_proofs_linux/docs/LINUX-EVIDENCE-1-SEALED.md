# LINUX-EVIDENCE-1 — SEALED LOCAL PROOF

**Sealed:** 2026-09-10  
**Status:** VALID  
**Scope:** Local WSL2 substrate, elevated/root execution

---

## Result

```
LINUX-EVIDENCE-1: VALID
E1-A (Normal Transport): VALID
E1-B (Forced Loss Accounting): VALID
```

---

## Provenance

| Field | Value |
|---|---|
| kernel | 6.6.87.2-microsoft-standard-WSL2 |
| python | 3.14.4 |
| dist | Ubuntu 26.04 LTS (x86_64) |
| privilege | root (elevated) |
| timestamp | 2026-09-10T04:17:05Z |
| bpf_c_sha256 | eb6373219b651d5752c045df93513844aecafe64156d2c8469e47f7d9f0beb76 |
| python_sha256 | 7edd8659f0b6dfd71cd8b6bcf764f0ec48c5bcdd77b50ca79e64cfdb3b239d28 |
| raw_result_sha | 028c282009de2f10b6280fa4e78988363fd554554dec0cadef58fb8c4e7ca7be |

---

## What Was Proven

**E1-A — Controlled exec observation (Normal transport)**
*   **Action:** Generated 100 `os.system("true")` calls within a specific target cgroup (filtered via `bpf_get_current_cgroup_id() == config_cgroup_id`).
*   **Result:** `seen_total` = 100, `submitted_total` = 100, `reserve_failures` = 0.
*   **Invariant:** `100 == 100 + 0`. Total events matching the native kernel filter were fully delivered to user space.

**E1-B — Forced telemetry saturation (Loss accounting)**
*   **Action:** Deliberately saturated the BPF ring buffer by shrinking it to 1 page (4KB) and generating 500 concurrent `os.system("true")` calls across 5 workers, while forcing a slow userspace consumer (50ms sleep per read).
*   **Result:** `seen_total` = 500, `submitted_total` = 27, `reserve_failures` = 473.
*   **Invariant:** `500 == 27 + 473`. The WNB-6 completeness property successfully held under massive saturation. The eBPF transport successfully provided an explicitly measured loss counter that balanced against the dropped records. Unaccounted loss remains zero.

---

## Conclusion

The eBPF observation channel was shown to be capable of filtering directly on the consequential execution identity (`cgroup_id` mapped from `LINUX-BINDING-0`), tracking events natively inside the kernel (`sched_process_exec`), and strictly bounding event loss through per-CPU drop counters if ring-buffer delivery fails.

*E1-C (Teardown) and E1-D (OOM) will follow to bind lifecycle correlations.*

---

## Program State After This Seal

```
LINUX-SPEC-MAP-0    ✅ SEALED
LINUX-BINDING-0     ✅ SEALED / VALID
LINUX-VRE-1         ✅ SEALED / VALID
LINUX-EVIDENCE-1    ✅ SEALED (Core Transport) / VALID
```
