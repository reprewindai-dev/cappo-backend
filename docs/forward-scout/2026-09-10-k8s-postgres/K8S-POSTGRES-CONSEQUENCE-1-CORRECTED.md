# K8S-POSTGRES-CONSEQUENCE-1 - Forward-Scout Specification

**Milestone Identifier:** `K8S-POSTGRES-CONSEQUENCE-1`  
**Current Status:** `DESIGN_VALID / SIMULATED / NOT YET SEALED`  
**Date:** 2026-09-10  
**Source Role:** Notebook forward-scout package for Gemini / Antigravity implementation  
**Truth Rule:** This document is a candidate architecture and hostile-test specification. It is not implementation evidence and must not be promoted to `VALID`, `VERIFIED`, `CERTIFIED`, or `SEALED` until the real Kubernetes/PostgreSQL implementation is executed and independently observed.

## 1. Doctrinal Statement and Scope

A Veklom lease is not permission to create a Pod. It is bounded authority for a specific workload realization to produce a specific consequence under an independently verified runtime envelope.

The target proof chain is:

```text
CapabilityLease
    -> admission-time workload intent binding
    -> persisted Kubernetes workload identity
    -> scheduled node identity
    -> container runtime identity
    -> Linux process / namespace / cgroup identity
    -> effective native resource and network state
    -> consequence dispatch
    -> PostgreSQL committed target state
    -> independent reconciliation
    -> teardown and durable evidence
```

This proof is intentionally split into two planes:

1. **Kubernetes execution plane:** proves that the workload that actually materialized is the workload CAPPO authorized and that its effective native bounds do not exceed the authorized envelope.
2. **PostgreSQL consequence plane:** proves whether the exact authorized transition committed, remained absent, or is still indeterminate after a crash or response loss.

Kubernetes admission, runtime materialization, target mutation, and consequence finality are separate observations. No single one may stand in for the others.

## 2. Kubernetes Identity Model

### 2.1 Admission-Time Binding

Admission-time state may bind only facts that actually exist at admission:

```text
lease_id
authority_digest
admission_request_uid
namespace
service_account
workload_spec_digest
expected_image_digest
requested_resource_envelope
expected_workload_identity_selectors
network_intent_digest
```

**Do not treat the AdmissionReview request UID as the eventual Pod UID.**

### 2.2 Post-Materialization Binding

After the object is persisted and scheduled, Veklom must independently resolve and bind:

```text
pod_uid
node_identity
container_runtime_id
actual_image_digest
workload_identity / SVID
host_pid
pid_starttime
pid_namespace_identity
cgroup_kernel_identity
namespace identities
effective memory / CPU / PID limits
effective network dataplane state
```

The authorization remains valid only if the post-materialization projection is compatible with the admission-time commitment and the authority has not widened.

## 3. Effective Authority and Native Projection

Canonical authority remains a monotonically non-widening intersection:

```text
EffectiveAuthority(t)
  = Biscuit
  intersect Package/Mount ceiling
  intersect Execution binding
  intersect Local policy(t)
  intersect Lifecycle(t)
  intersect Time bounds(t)
  intersect Offline bounds(t)
  intersect Budgets(t)
```

The native runtime projection must satisfy:

```text
EffectiveNativeProjection is a subset of AuthorizedEnvelope
```

This is a semantic containment claim, not a claim about physical hardware backing.

## 4. Predator Attack Falsification Matrix

The following eight attacks are the **required implementation battery**. A mocked or synthetic PASS is not sufficient.

### Plane 1 - Kubernetes

| Vector | Attack | Required hostile stimulus | Required measured proof | Pass condition |
|---|---|---|---|---|
| `K8S-ATTACK-01` | Admission-to-runtime identity substitution | Admit one workload intent, then attempt to substitute a different persisted Pod/runtime identity | Admission commitment, persisted Pod UID, node ID, container runtime ID, image digest, workload identity, cgroup identity | Substituted runtime receives no inherited authority and dispatch is denied |
| `K8S-ATTACK-02` | Native memory-envelope widening | Workload attempts to exceed its authorized memory ceiling | Exact cgroup identity, `memory.max` readback, `memory.events` delta including `oom_kill`, process termination evidence | Effective limit matches authorized ceiling and hostile workload is contained |
| `K8S-ATTACK-03` | Unauthorized egress | Workload attempts traffic outside the authorized destination set | Effective dataplane rule/program identity, blocked attempt, control connection, recovery/control observation | Unauthorized flow is blocked by the effective node dataplane; authorized control path still works |
| `K8S-ATTACK-04` | Stale Pod/name/ServiceAccount reuse | Delete workload A, recreate workload B with same human-readable name and identity labels | Old/new Pod UIDs, old/new cgroup kernel identities, lifecycle evidence, authority lookup | B inherits zero authority from A; fresh binding is required |

### Plane 2 - PostgreSQL consequence finality

| Vector | Attack | Required hostile stimulus | Required measured proof | Pass condition |
|---|---|---|---|---|
| `PG-ATTACK-01` | Crash after target commit / before executor acknowledgement | Deterministically commit target mutation, then lose executor acknowledgement | Dispatch boundary evidence, target commit evidence, executor transport failure, CAPPO state, target request count | CAPPO enters `OUTCOME_UNKNOWN`; authority is fenced; no blind redispatch occurs |
| `PG-ATTACK-02` | Independent E3 reconciliation | Query target using a server-side observer independent of executor narration | `transition_id`, `request_commitment`, target transaction/commit identity, mutation digest, committed target state | Matching committed transition -> `RECONCILED_SUCCEEDED`; authoritative absence -> `RECONCILED_FAILED` |
| `PG-ATTACK-03` | Observer/target unavailable | Make reconciliation path unavailable after outcome becomes ambiguous | Reconciliation request/response plus persisted CAPPO state | `/reconcile` returns `503 RECONCILIATION_UNAVAILABLE`; authority remains fenced; subsequent dispatch returns `423 AUTHORITY_LOCKED` |
| `PG-ATTACK-04` | Post-finality exact replay | Re-submit the same committed consequence identity | Same `transition_id`, same `request_commitment`, same consumed authority, new dispatch attempt | Redispatch is denied; target-side consequence count remains exactly one |

## 5. PostgreSQL E3 Target Contract

`execution_id` is execution context, **not** the canonical consequence identity.

The durable consequence identity should be centered on the transition:

```text
transition_id
request_commitment
authority_digest
execution_id
attempt_id
target_resource
mutation_digest
effect_commitment
target_transaction_id
committed_at
```

A candidate target-side schema is:

```sql
CREATE TABLE veklom_consequence_audit (
    transition_id          TEXT PRIMARY KEY,
    request_commitment     TEXT NOT NULL,
    authority_digest       TEXT NOT NULL,
    execution_id           TEXT NOT NULL,
    attempt_id             TEXT NOT NULL,
    target_resource        TEXT NOT NULL,
    mutation_digest        TEXT NOT NULL,
    effect_commitment      TEXT NOT NULL,
    target_transaction_id  TEXT,
    affected_rows          INTEGER NOT NULL,
    committed_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
```

### Atomicity Requirement

The business mutation and the `veklom_consequence_audit` record must commit in the **same PostgreSQL transaction**:

```text
BEGIN
    business mutation
    consequence audit record
COMMIT
```

A design where the business mutation can commit and the audit record can be lost is **INVALID** for E3 reconciliation.

## 6. Reconciliation Semantics

The canonical ambiguity flow is:

```text
AUTHORIZED
    -> STARTED / DISPATCHING
    -> target may commit
    -> executor loses confirmation
    -> OUTCOME_UNKNOWN
    -> authority fenced
    -> independent target observation
```

Resolution semantics:

```text
observer reachable + matching committed transition
    -> RECONCILED_SUCCEEDED

observer reachable + authoritative proof transition did not occur
    -> RECONCILED_FAILED

observer unavailable / target unavailable
    -> HTTP 503 RECONCILIATION_UNAVAILABLE
    -> state remains unresolved
    -> authority remains fenced

redispatch while unresolved
    -> HTTP 423 AUTHORITY_LOCKED
```

`503` describes inability to reconcile.  
`423` describes inability to dispatch because the authority is locked.

## 7. Replay Identity

Exact replay is defined by the consequence contract, not by `execution_id` alone.

At minimum, replay comparison must preserve:

```text
transition_id
request_commitment
authority_digest
```

A legitimate new consequence may reuse an execution context only if it has a distinct authorized transition. A legitimate retry after a proven failure uses a new `attempt_id` without silently widening or replacing the authority grant.

For an ambiguous outcome, no retry is permitted until reconciliation resolves the prior attempt.

## 8. Evidence Requirements

A PASS requires raw, reproducible evidence. Status strings such as `BLOCKED_BY_EBPF_ACL`, `TERMINATED_BY_OOM_KILLER`, or `RECONCILED_SUCCEEDED` are assertions until backed by independent observations.

The evidence packet should include, as applicable:

```text
transition_id
request_commitment
lease_id / authority_digest
execution_id
attempt_id
admission_request_uid
pod_uid
node identity
container runtime ID
actual image digest
workload identity / SVID
PID + starttime + pidns identity
cgroup kernel identity
effective cgroup resource readback
effective network enforcement identity
telemetry loss counters
dispatch boundary timestamps
target transaction / commit identity
target state before / after
target request count
reconciliation result
teardown evidence
probe / raw-result hashes
runtime / kernel / Kubernetes / PostgreSQL versions
privilege context
```

Local keyed MACs may protect local evidence integrity, but they are not themselves the canonical execution identity and do not substitute for externally verifiable evidence where that assurance is required.

## 9. Seal Gate

This milestone may be promoted from:

```text
DESIGN_VALID / SIMULATED
```

to:

```text
MEASURED -> VALID / INVALID / INDETERMINATE
```

only after all of the following are true:

- real Kubernetes API server / scheduler / kubelet or equivalent local cluster components are exercised;
- real container runtime and Linux cgroup identities are read back;
- real effective network enforcement is tested with a causal control/deny/recovery sequence;
- real PostgreSQL mutation and target-side audit record commit atomically;
- crash-after-commit-before-ack is injected deterministically;
- reconciliation is performed independently of executor self-report;
- `503` reconciliation-unavailable and `423` redispatch-lock semantics are measured separately;
- replay is keyed to the consequence transition rather than `execution_id` alone;
- raw evidence and hashes are preserved;
- no claim is marked `SEALED` until the exact implementation commit and evidence packet are repository-canonical.

## 10. Gemini / Antigravity Handoff

Implement this specification as a falsifiable proof program.

Rules:

1. Treat all Notebook results as hypotheses, not implementation evidence.
2. Preserve useful architecture but correct assumptions against the actual Kubernetes, Linux, PostgreSQL, CAPPO, cAPI, and VRE runtime.
3. Do not infer a PASS from an API response alone; obtain independent native or target-side observation.
4. Do not substitute human-readable names for kernel/runtime object identity.
5. Do not use `execution_id` as the global idempotency/replay key.
6. Do not collapse `503 RECONCILIATION_UNAVAILABLE` into `423 AUTHORITY_LOCKED`.
7. Return every attack as `VALID`, `INVALID`, or `INDETERMINATE`.
8. Seal only after the real implementation, hostile battery, raw evidence, and exact commit SHA are preserved together.

**Current verdict:** `FORWARD-SCOUT PACKAGE READY FOR IMPLEMENTATION`
