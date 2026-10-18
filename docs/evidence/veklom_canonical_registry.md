# Veklom Canonical Conformance Registry
**Last Updated:** 2026-08-27

This is the single, canonical registry for all Veklom conformance claims. Every claim points to its specific evidentiary gate.

## 1. FOUNDATION TRACK
Validates the fundamental cryptographic and execution substrates.

| Gate | Name | Status | Evidence |
|---|---|---|---|
| **G0A** | Local Runtime Baseline | **PASS** | `test_g0a_*.py` |
| **G0B** | Sovereign Cryptographic Conformance | **IN PROGRESS** | `test_g0b*_*.py` (G0B.2 SVID Enforcement pending) |
| **G1** | Offline Sovereign Execution | **PENDING** | Full WAN-OFF gate validation |

## 2. CONSTITUTIONAL / ADVERSARIAL TRACK
Validates the system's resistance to hostile workloads, bypassing, and epistemic corruption.

| Gate | Name | Status | Evidence |
|---|---|---|---|
| **P1** | Authority Boundary | **CLOSED** | 3/3 hostile tests pass (`test_g1_p1_hostile_workload*.py`) |
| **P2** | Consequence Dominance | **CLOSED** | 3/3 hostile tests pass (`test_cd_*.py`) |
| **P3** | Authority Monotonicity | **CLOSED** | 4/4 hostile tests pass (`test_am_*.py`) |
| **P4** | Offline / Identity / Replay Integrity | **CLOSED** | 3/3 hostile tests pass (`test_zra_*.py`, `test_p4_*.py`) |
| **P5** | Truth-State / Epistemic Integrity | **CLOSED** | 21/21 hostile tests (`test_p5_*.py`)<br>TLC Model Check (576 states, 0 violations)<br>Python ↔ TLA+ Trace Correspondence Verified |

## 3. SYSTEM TRACK
Validates assembled integration, independent reproducibility, and release readiness.

| Gate | Name | Status | Evidence |
|---|---|---|---|
| **E2E** | Assembled Hostile Conformance | **PENDING** | - |
| **IR**  | Independent Reproduction | **PENDING** | - |
| **RC**  | Release Qualification | **PENDING** | - |

---

### Architectural Distinctions
* **G0B.5 vs P5:** G0B.5 ensures Veklom can create cryptographically sound, tamper-evident records. P5 ensures the *proposition* inside those records is actually true, bound to reality, and authorized. Cryptography preserves the claim; P5 prevents the lie.
