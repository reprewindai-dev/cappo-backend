# PRODUCT-0 Notebook -> Antigravity Coordination Handoff

Date: 2026-09-09
Status: ACTIVE HANDOFF - DO NOT TREAT AS MERGE-COMPLETE
Branch purpose: make Notebook/Nova discoveries visible to Antigravity without forcing duplicate work.

## Operating rule

Notebook/Nova may discover, structure, or locally prove a layer before Antigravity reaches it. Antigravity should consume the proven result and work only the unresolved production delta.

Classification used in this handoff:
- ALREADY PROVEN: do not re-prove unless a production integration falsifier requires it.
- NEEDS WIRING: invariant is proven; move it into the canonical runtime path.
- NEEDS GITHUB LANDING: local work exists but is not repository-canonical yet.
- NEEDS LIVE PROOF: static/harness proof exists; live port-8002/runtime evidence is still pending.

## PRODUCT-0 baseline

The defined A-L PRODUCT-0 proof battery remains CLOSED at its recorded proof surfaces:
A unauthorized DENY; B authorized bounded consequence; C exact replay DENY; D authority mutation DENY; E lease/execution substitution DENY; F envelope substitution DENY; G execution revocation DENY; H hard deadline SIGKILL; I cgroup memory OOM; J Wasmtime fuel trap; K crash/restart WAL + OUTCOME_UNKNOWN lock; L reconciliation unavailable + authority lock.

Do not re-letter or redefine this battery.

## Notebook/Nova contract-layer work - ALREADY PROVEN LOCALLY

Notebook produced a repository-safe PRODUCT-0 contract conformance gate and reported 11/11 local contract tests passing with 0 failures and 0 errors. The local result is STATIC/LOCAL only and explicitly leaves live runtime proof pending.

The intended three-layer chain is:
PROVEN A-L INVARIANTS -> OPENAPI/ASYNCAPI CONTRACT -> CAPPO RUNTIME CONFORMANCE.

Static contract rules already modeled:
1. exact denial-class mapping;
2. HTTP 423 for already-locked authority/execution state;
3. HTTP 503 only for unavailable target/observer infrastructure;
4. browser responses do not leak raw Biscuit/private authority material;
5. client-supplied Veklom-Authority is not trusted machine authority;
6. target observer endpoints are server-bound, not caller-supplied URLs;
7. OpenAPI response-schema conformance;
8. AsyncAPI WAL/VRE/reconciliation event conformance.

## Canonical contract corrections - USE THESE, NOT THE EARLIER COMBINED NOTEBOOK YAML

The corrected contract model is:
- separate OpenAPI and AsyncAPI documents;
- raw Biscuit authority remains server-side for the browser/session path;
- reconciliation accepts trusted target binding/resource identifiers, never an arbitrary caller URL;
- POST /consequence/reconcile may return 503 when the observer is unavailable and persists/retains RECONCILIATION_UNAVAILABLE;
- POST /consequence/dispatch returns 423 when a client attempts redispatch while authority is locked in OUTCOME_UNKNOWN or RECONCILIATION_UNAVAILABLE;
- evidence fields do not claim E4 Merkle/SCITT proof unless that proof actually exists;
- fuel exhaustion is a Wasmtime trap classification, not hard-coded exit code 137;
- replay is normative single-use authority: validate first, claim atomically second, never resurrect consumed authority by restart/cache expiry/reconciliation/retry;
- E3 observer independence is normative and distinct from executor success narration.

## Antigravity replay work - NEEDS GITHUB LANDING

Antigravity reports local changes in C:\Users\antho\.windsurf\cappo-backend:
- cappo_backend/services/capability_handler.py: replay fence + constructor injection;
- cappo_backend/api/routers/exec_router.py: handler construction;
- tests/test_c_capability_lease_replay.py: canonical Test C boundary test.

Important correction already accepted: replay_cache must NOT live on VerifiedExecutionContext. Mutable replay infrastructure belongs on CapabilityHandler (constructor/service dependency); the context remains immutable verified data.

Durability caveat: reusing the exact same MockReplayCache object across a new handler instance does NOT prove process-restart durability. For C6 durable restart proof use a durable backend/new client instance (Redis, DB, or durable lease/receipt state) and show replay remains denied after process-memory loss.

## Contract conformance runner - DO NOT REDO STATIC WORK

Notebook's latest runner already moved away from /workspace/scratch and resolves REPO_ROOT dynamically. Keep that work.

Before making it a required CI merge gate, Antigravity should only close these deltas:
1. success must require skipped == 0 if PRODUCT-0 policy is zero skips;
2. per-rule GREEN status must require that the expected named test actually existed, ran, was not skipped, and passed - absence must not render GREEN;
3. canonical filenames must be contracts/cappo-consequence-dispatch.openapi.yaml and contracts/cappo-consequence-events.asyncapi.yaml;
4. live runtime proof remains pending until a real CAPPO integration service is started and exercised.

## GitHub Actions draft - NEEDS LIVE WIRING

Do not make a workflow required if it only echoes live-runtime success.

The merge gate may print CONTRACT + RUNTIME CONFORMANCE: PASSED / MERGE GATE: SATISFIED only after all of these commands actually execute successfully:
- start real CAPPO integration runtime on port 8002;
- run hostile port-8002 contract battery;
- seal raw responses/receipts;
- fail non-zero on any mismatch.

Until then the correct terminal state is:
LOCAL CONTRACT GATE: PASSED
STATUS: READY FOR LIVE RUNTIME CONFORMANCE

## Next Antigravity execution delta

1. Land local CapabilityHandler replay changes on this or a follow-on branch with a commit SHA.
2. Preserve the canonical A-L numbering and evidence pointers.
3. Add/copy the Notebook contract test modules if they are not already in the repo.
4. Finish the two static-runner correctness fixes (zero skips + named-test execution tracking).
5. Wire the real port-8002 integration runtime and hostile contract battery.
6. Seal raw JSON/runtime receipts in docs/evidence/.
7. Push branch and provide exact commit SHA(s).
8. Only then request merge/required-check promotion.

## Claim discipline

PRODUCT-0 A-L proof battery: CLOSED at defined proof surfaces.
Canonical production wiring: accepted only where the corresponding branch/commit/runtime evidence is visible.
Bare-metal multi-tenant isolation: NOT PROVEN.
E4 public transparency/SCITT inclusion: NOT PROVEN.
Generic exactly-once side effects: NOT PROVEN; claim fail-closed ambiguity locking and single-use authority instead.
Zero transient memory overshoot: NOT PROVEN.
