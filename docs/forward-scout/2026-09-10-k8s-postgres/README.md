# Kubernetes + PostgreSQL Forward-Scout Package

**Status:** DESIGN_VALID / implementation handoff only  
**Purpose:** Keep Notebook/architecture work ahead of Gemini/Antigravity without confusing forward-scout material with measured or sealed evidence.

## Operating model

```text
Notebook / discovery
    -> candidate architecture
    -> mocked flows
    -> predicted failure modes
    -> predator batteries
           |
           v
Forward-scout package in GitHub
           |
           v
Gemini / Antigravity
    -> inspect real repo/runtime
    -> correct assumptions
    -> implement smallest real slice
    -> execute hostile battery
    -> preserve raw evidence
           |
           v
VALID / INVALID / INDETERMINATE
           |
           v
Seal only when implementation commit + evidence packet are canonical
```

Notebook predicts. Implementation attacks. Evidence decides.

## Package contents

- `K8S-POSTGRES-CONSEQUENCE-1-CORRECTED.md` - corrected end-to-end Kubernetes/PostgreSQL consequence proof specification and seal gates.
- `postgres_e3_rls_schema_CORRECTED.sql` - hardened E3/RLS target-side schema with transition-centric consequence identity, protected tenant binding, FORCE RLS, separated writer/observer/workload roles, and atomic mutation+audit requirements.
- `postgres_rls_predator_spec_CORRECTED.json` - 8-vector hostile RLS/E3 battery for the real PostgreSQL rerun.
- `postgres_e3_rls_evidence_review.md` - defects found in the Notebook-generated RLS receipts and exact rerun requirements.

## Execution order for Gemini / Antigravity

Do not interrupt the current task to start this package. When the current task is complete:

1. Confirm current repository SHA and prerequisite authority/adapter work.
2. Read `K8S-POSTGRES-CONSEQUENCE-1-CORRECTED.md` completely.
3. Implement the smallest real Kubernetes identity/envelope slice needed for the hostile battery.
4. Implement the PostgreSQL E3 target adapter against the corrected schema.
5. Execute the Kubernetes predator cases using native runtime evidence.
6. Execute the PostgreSQL E3/RLS predator cases against a real PostgreSQL server.
7. Return every case as VALID / INVALID / INDETERMINATE.
8. Preserve raw receipts, environment/runtime versions, timestamps, hashes, and exact implementation commit SHA.
9. Only then update or supersede milestone certification language.

## Non-negotiable corrections

- Admission request UID is not the final Pod UID.
- Runtime identity must be joined after materialization to Pod UID, node, container runtime identity, image digest, process/namespace/cgroup identity, and effective bounds.
- `execution_id` is execution context, not the global consequence/idempotency key.
- Consequence identity is transition-centric (`transition_id`, `request_commitment`, `authority_digest`).
- Business mutation and authoritative E3 receipt must commit in the same PostgreSQL transaction.
- Reconciliation infrastructure unavailability is HTTP 503; redispatch while authority is locked is HTTP 423.
- Workload roles may not manufacture, update, or delete authoritative E3 receipts.
- Tenant isolation must not trust a worker-settable custom GUC as the sole security boundary.
- RLS tests require a positive control proving the hidden target row actually exists.
- A status string is not evidence. Native/target-side independent observation is required.

## Truth precedence

This folder is forward-scout material. It does not override measured runtime behavior, canonical implementation, or sealed evidence. If any assumption here conflicts with the real runtime, record the conflict and let the measured result win.
