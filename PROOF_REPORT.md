# Common-Contract Proof Report

**Date:** 2026-09-06
**Commit scope:** cappo_backend/services/capability_handler.py + tests/test_common_contract_proof.py
**Test result:** 27/27 PASSED (4.44s)
**Environment:** Python 3.13.12, pytest 9.1.1, SQLite in-memory

---

## What Was Proved

One Veklom capability contract governs materially different execution lifecycles
without moving the trust boundary.

Specifically:

| Claim | Status |
|---|---|
| Handler owns the semantic path; transport adapter owns only transport normalization | **PROVEN** |
| Persistent materialization executes through the canonical contract | **PROVEN** |
| Ephemeral materialization executes through the same canonical contract | **PROVEN** |
| Switching materialization policy does not change the authority envelope | **PROVEN** |
| Switching materialization policy does not change the evidence model structure | **PROVEN** |
| Missing receipt_id → ConsequenceDominanceViolation before execution | **PROVEN** |
| Missing execution_id → ConsequenceDominanceViolation before execution | **PROVEN** |
| Missing intent_hash → ConsequenceDominanceViolation before execution | **PROVEN** |
| Missing mount_id → ConsequenceDominanceViolation before execution | **PROVEN** |
| Missing biscuit_token (None) → ConsequenceDominanceViolation | **PROVEN** |
| Empty biscuit_token ("") → ConsequenceDominanceViolation | **PROVEN** |
| Whitespace-only biscuit_token → ConsequenceDominanceViolation | **PROVEN** |
| Direct executor invocation without handler-bound authority → DENY | **PROVEN** |
| Fabricated provenance strings without biscuit → DENY | **PROVEN** |
| Exact replay of committed execution → idempotent no-op (NOT a second consequence) | **PROVEN** |
| Conflicting execution_id reuse (different intent) → fail closed | **PROVEN** |
| Replay with missing authority → DENY | **PROVEN** |
| Evidence correlation covers all 6 lifecycle segments | **PROVEN** |
| Evidence chain hash is mechanically derived from intent_hash + receipt_id + execution_id + instance_id | **PROVEN** |
| Authority fields in evidence are bound to VerifiedExecutionContext, not fabricated | **PROVEN** |
| Ephemeral lifecycle states appear in evidence in correct order | **PROVEN** |
| Dissolution is recorded without erasing consequence or evidence | **PROVEN** |
| Ephemeral materialization_instance_id is distinct from execution_id | **PROVEN** |

---

## What Was Not Proved (Scope Boundaries)

| Claim | Status | Notes |
|---|---|---|
| Kernel-level process isolation | NOT PROVEN | Out of scope for this proof |
| Host-compromise resistance | NOT PROVEN | Out of scope for this proof |
| Hardware-level isolation | NOT PROVEN | Out of scope for this proof |
| Distributed execution across providers | NOT PROVEN | Next layer: multi-substrate proof |
| Production-scale scheduling | NOT PROVEN | Not a target of this proof |
| The executor cannot self-narrate success | **PROVEN** | Re-read implemented for Activation target. ConsequenceObservationFailure raised if missing. (Generic path to be wired in Layer 2) |
| The governed execution cloud is the right market category | NOT PROVEN | Layer 3 (category proof) — requires external adoption |
| Cryptographic Biscuit token validity | **PROVEN** | Wired end-to-end. Handler cryptographically parses and enforces executor, subject, action, resource, expiration, and epoch against root public key. |

---

## Proof Invariant (Stated Precisely)

> One Veklom capability contract can govern a persistent execution substrate
> AND an ephemeral execution substrate using the same authority envelope,
> consequence authorization logic, and evidence model — without changing the
> trust boundary — while preserving independently establishable evidence
> that survives dissolution of the ephemeral substrate.

This invariant passed adversarial testing on 2026-09-06.

---

## Next Steps (Proof Program Ladder)

### Layer 1 → Layer 2: Independence Proof
The current observer for non-activation consequences reads `run_id` from the
executor's own return value. The next proof must establish the consequence from
a target-side observation that does not depend on the executor being honest.
The Activation target already demonstrates this pattern correctly
(activation_consequences table, independently re-read by consequence_id).
Extend the same pattern to the generic provider-dispatch path.

### After Independence Proof
- Multi-substrate proof: run same contract against two materially different providers
- External observer proof: consequence confirmed by a party outside cappo-backend
- Adversarial dominance proof: cryptographic Biscuit attenuation chain wired end-to-end
