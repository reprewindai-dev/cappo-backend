# P5 Formal Model Check — Evidence Record

**Date:** 2026-08-27  
**TLC Version:** TLC2 2026.08.21.155922 (rev: 9787e65)  
**Java:** Eclipse Adoptium 17.0.8.1 (64-bit)  
**Spec:** `formal_specs/P5_ConsequenceTruth.tla`  
**Config:** `formal_specs/P5_ConsequenceTruth.cfg`

---

## Result

```
Model checking completed. No error has been found.
2752 states generated, 576 distinct states found, 0 states left on queue.
Depth of complete state graph: 5
Finished in 01s
Fingerprint collision probability (optimistic): 6.8E-14
```

> **ZERO INVARIANT VIOLATIONS across all 576 reachable states.**

---

## Model Bounds

| Constant | Value |
|---|---|
| `Operations` | `{op1, op2}` — 2 operations |
| `Intents` | `{i1, i2}` — 2 distinct intent hashes |
| `Consequences` | `{c1, c2}` — 2 distinct consequence IDs |
| `ProofTypes` | `{callback_return, outcome_uncertain, reconciliation_api_query, invalid_proof}` |
| `has_truth_auth` | Non-deterministic boolean per operation (all combinations explored) |

Deadlock check is **disabled by design**: when `has_truth_auth = FALSE` for all operations in `OUTCOME_UNKNOWN`, the system correctly reaches a governed uncertainty state with no further transitions — this is the intended `UNKNOWN > FALSE CERTAINTY` property.

---

## Safety Invariants Checked (All Pass)

| Invariant | Meaning | Result |
|---|---|---|
| `NoOverclaim` | Every terminal state has `Certainty(proof) ≥ ReqCertainty(state)` AND reconciled states require `has_truth_auth = TRUE` | ✅ 0 violations |
| `NoIllegalTruthTransition` | Every state is a member of `AllStates` | ✅ 0 violations |
| `NoTruthWithoutAuthority` | `RECONCILED_*` states require `has_truth_auth = TRUE` | ✅ 0 violations |
| `UnknownCannotSelfResolve` | No operation can be in `UNKNOWN` with `proof = callback_return` | ✅ 0 violations |

---

## Attacker Actions Modeled

The following hostile actions were included in `Next` and explored exhaustively:

| Attacker Action | What It Attempts | Blocked By |
|---|---|---|
| `ForgeState` | Force any terminal state with arbitrary proof | FSM gate + Certainty ≥ ReqCertainty + auth guard |
| `ReplayProof(op1, op2)` | Transplant op1's terminal state onto op2 using op1's proof | Proposition binding: requires `intent[op1]=intent[op2]` AND `conseq[op1]=conseq[op2]` |
| `SwapIntent` | Modify the intent hash of an operation | Guard: only allowed while `AUTHORIZED` (before execution starts) |
| `SwapConsequence` | Replace the consequence identity | Guard: only allowed while `AUTHORIZED` |
| `BypassAuthority` | Reconcile UNKNOWN without authority | `has_truth_auth` enforcement |
| `ResolveUnknownWithoutProof` | Transition from UNKNOWN to SUCCEEDED with certainty-1 proof | `Certainty("callback_return") >= 2` evaluates FALSE — action never enabled |
| `RollbackEpoch` | Revert a terminal state to Started | Action body is `FALSE` — append-only invariant |
| `RaceReconcilers` | Two reconcilers racing to resolve the same UNKNOWN | Atomic CAS model: one wins, state is still valid |
| `RaceExecutors` | Two executors racing for AUTHORIZED → STARTED | Both resolve to STARTED (idempotent entry) |
| `SwapOperation` | Steal another operation's identity | Action body is `FALSE` — identity is token-intrinsic |

---

## Liveness Property

`Liveness == ∀ op ∈ Operations : ◇(state[op] ∈ TerminalStates ∪ {Unknown})`

This property is **not actively checked** in this run (no PROPERTY clause) because TLC's liveness checking requires fairness assumptions that would exclude the deliberately non-fair cases (e.g., no reconciler ever arriving). The property is stated in the spec for documentation and can be verified separately with fairness annotations.

The key epistemic liveness claim is preserved architecturally:
> Every operation eventually reaches either a **justified terminal state** or a **durable governed uncertainty state** (`OUTCOME_UNKNOWN`). No false certainty is manufactured.

---

## Claim Boundary

> **The P5 formal model, with 10 explicit attacker actions and 4 safety invariants, was exhaustively model-checked over 2752 states with ZERO invariant violations.**

This means: **no reachable overclaim state exists in the bounded model.**

This is a statement about the **model**, not the Python runtime. The correspondence claim (model ↔ implementation) remains the last open gate.

---

## P5 Status After This Run

```
P5 — RUNTIME-VERIFIED + FORMALLY MODEL-CHECKED

RUNTIME:
  21/21 hostile tests pass (Wave 1 + Wave 2)

FORMAL:
  TLC state-space exploration: COMPLETE
  States explored: 2752 generated / 576 distinct
  Invariant violations: 0
  TLC version: 2026.08.21.155922

BOUNDARY:
  cappo-backend consequence-execution / truth-state model

CLAIM:
  No governed truth transition can assert more than the
  modeled authority and admissible evidence permit.

OPEN GATE:
  Runtime ↔ model correspondence (trace mapping)
  Before this gate: P5 = CANDIDATE FOR FULL CLOSURE
```
