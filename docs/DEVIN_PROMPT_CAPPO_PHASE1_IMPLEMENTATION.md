# Devin Prompt — CAPPO Phase 1 Implementation

You are implementing Phase 1 of the CAPPO runtime track for Veklom.

Read this entire prompt carefully before doing any work.

## Why this prompt is structured this way
Devin works best when the task is specific, references existing code patterns, defines clear success criteria, and includes validation steps.[cite:217][cite:223][cite:220]

## Scope
Implement **Phase 1 only** of `ExecutionIdentityV1`.

Phase 1 includes:
1. schema and object model changes
2. orchestrator minting of `ExecutionIdentityV1`
3. `MCPGateway` validation support
4. `/v1/exec` closure or temporary enforcement path, only if required to complete the LAW 0 path for this phase
5. production guard against simulated PGL fallback

Do **not** do broader refactors, repo creation, frontend work, or server operations.

## Truth order
Follow this precedence exactly:

1. Human instructions in this task
2. `CAPPO_GOLD_BLUEPRINT.md`
3. `CAPPO_NAMING_AND_SCOPE.md`
4. `AGENT_OPERATING_RULES.md`
5. `CAPPO_EI_LINEAGE_AND_GAP_BRIEF.md`
6. `CAPPO_EXECUTIONIDENTITYV1_IMPLEMENTATION_PLAN.md`
7. Verified current code
8. Other docs for context only

If any item lower in the list conflicts with a higher item, the higher item wins.

## Mandatory pre-work
Before changing code, do all of the following:

1. Read these docs in full:
   - `CAPPO_GOLD_BLUEPRINT.md`
   - `CAPPO_NAMING_AND_SCOPE.md`
   - `AGENT_OPERATING_RULES.md`
   - `CAPPO_EI_LINEAGE_AND_GAP_BRIEF.md`
   - `CAPPO_EXECUTIONIDENTITYV1_IMPLEMENTATION_PLAN.md`
2. Re-read these code files in full:
   - `backend/db/models/pgl.py`
   - `backend/services/orchestrator.py`
   - `backend/core/security/mcp_gateway.py`
   - `backend/api/routers/exec_router.py` or equivalent current exec router location
   - `backend/services/pgl_client.py` or equivalent current PGL client location
   - `backend/middleware/middlewares.py` or equivalent current middleware location
3. Check whether the docs still match the code for the areas you are touching.
4. If any contradiction, stale statement, or naming drift exists, stop immediately and report it before proceeding.

Do not guess.
Do not silently reinterpret architecture.
Do not modify canonical docs unless explicitly instructed by the human doc owner.

## Verified architecture facts
Treat these as already confirmed unless the code has changed since verification:

- `PGLCertificate` is the seed provenance object.
- `VeklomRunStateMachine` enforces the governed execution path.
- `commit_run()` mints the PGL pre-certificate.
- `MCPGateway` currently has zero EI/PGL/SEKED validation.
- `/v1/exec` currently bypasses the orchestrator.
- PGL simulation fallback can produce `persisted: False` certificates.

## Goal
Close the first LAW 0 enforcement gaps by introducing `ExecutionIdentityV1` into the real runtime path without broad redesign.

## Required implementation tasks
Complete the following tasks in order.

### Task 1 — Data model
Add the minimum persistence needed for `ExecutionIdentityV1`.

Requirements:
- Add `execution_identity` to the active run model using the project’s existing JSON/JSONB conventions.
- Add a persistent `execution_identities` table or equivalent model using current naming and migration patterns.
- Preserve existing runtime behavior outside the new LAW 0 checks.
- Do not rename unrelated models or fields.

Reference patterns:
- `PGLCertificate`
- existing SQLAlchemy models and migrations in the backend

Success criteria:
- migrations apply cleanly
- model imports resolve cleanly
- no unrelated schema drift

### Task 2 — Canonical ExecutionIdentityV1 object
Implement a canonical builder for `ExecutionIdentityV1`.

Requirements:
- Use the field set defined in `CAPPO_GOLD_BLUEPRINT.md`
- Build the object from the orchestrator context after governance and commit
- Canonicalize the payload before hashing
- Compute `hash`
- Compute `signature` using the configured signing mechanism or a clearly isolated placeholder adapter if the signing mechanism does not yet exist
- Make the builder easy to test in isolation

Success criteria:
- object creation is deterministic for the same inputs
- missing required inputs fail loudly
- builder is unit-testable

### Task 3 — Orchestrator mint point
Mint `ExecutionIdentityV1` in the orchestrator at the exact phase boundary:

`GOVERNED -> COMMITTED -> [mint EI] -> ROUTED -> EXECUTING`

Requirements:
- mint only after `govern_run()` and `commit_run()` succeed
- read the pre-cert id from the real run state
- attach the EI to the run
- persist the EI record
- do not create alternate mint paths elsewhere unless absolutely necessary for compatibility

Reference patterns:
- existing run state transitions in `orchestrator.py`

Success criteria:
- orchestrated runs mint EI before routing
- failed mint blocks further execution
- state progression remains coherent

### Task 4 — MCP Gateway validation
Add `ExecutionIdentityV1` validation support to `MCPGateway`.

Requirements:
- implement a method equivalent to `require_execution_identity(...)`
- validate the nine rules from the implementation plan:
  1. real persisted PGL reference
  2. hash alignment with PGL provenance
  3. directive permits execution
  4. TTL not expired
  5. scope covers requested action
  6. budget covers action cost
  7. delegation depth within max
  8. signature and hash verify
  9. identity not revoked
- reject before side effects occur
- use current error-handling conventions where possible

Success criteria:
- valid EI passes
- each invalid EI failure mode returns a specific reason
- no side-effecting path guarded by the gateway can proceed without EI once enabled

### Task 5 — `/v1/exec` enforcement path
Prefer **Option A** from the implementation plan.

Option A:
- route `/v1/exec` through the orchestrator so it inherits governance, PGL, EI minting, execution, and attestation

If Option A is too disruptive for this phase, use **Option B** only if you clearly document why in your final report:

Option B:
- add a temporary enforcement dependency or middleware so `/v1/exec` requires valid EI before execution

Requirements:
- no silent bypass remains on the chosen path
- preserve current response shape as much as practical
- avoid introducing a third execution path

Success criteria:
- `/v1/exec` no longer permits ungoverned execution on the implemented path
- regression tests cover the protected behavior

### Task 6 — Production fallback guard
Forbid simulated PGL fallback in production.

Requirements:
- add the environment-guarded constructor protection described in the implementation plan
- make the behavior explicit and loud
- do not break local development defaults unless the env var is enabled

Success criteria:
- production mode with missing DB session fails fast
- local development remains usable when the env var is not enabled

## Testing requirements
You must test your work.

At minimum:
1. Add unit tests for the EI builder
2. Add tests for gateway validation success and failure cases
3. Add regression tests for `/v1/exec` protection
4. Add tests for production fallback guard behavior
5. Run the relevant backend test suites
6. Run any migration checks or startup validation needed to prove imports and boot still work

Use existing test files as patterns rather than inventing a new testing style.

## Reporting requirements
Before opening any PR or finalizing work, provide a report with these sections:

1. **Files changed** — list every file changed and why
2. **Implementation choices** — especially whether `/v1/exec` used Option A or Option B
3. **Validation results** — exact test commands run and whether they passed
4. **Open risks** — anything not fully closed in this phase
5. **Doc/code mismatches found** — even if none

## Hard constraints
- Do not edit canonical docs unless explicitly instructed by the human doc owner.
- Do not touch servers, deployment configs, or infrastructure unless this task explicitly requires a small local code/config change for the production fallback guard.
- Do not create a new `cappo-runtime` repo.
- Do not rename the public brand `Veklom`.
- Do not use old runtime names for new CAPPO implementation objects.
- Do not widen scope into frontend, marketplace, or unrelated cleanup.
- Do not bypass LAW 0 checks for convenience.

## Done definition
This task is done only when all of the following are true:

- `ExecutionIdentityV1` has a real model/object path and persistence
- orchestrated runs mint EI at the correct point
- `MCPGateway` can enforce EI validation
- `/v1/exec` no longer remains an ungoverned bypass on the implemented path
- production fallback guard exists for simulated PGL use
- tests exist and pass for the implemented behavior
- final report explains exactly what changed

## Work style
Be specific, use existing code patterns, validate your own work, and stop at checkpoints when architecture facts are unclear.[cite:217][cite:223][cite:220]
