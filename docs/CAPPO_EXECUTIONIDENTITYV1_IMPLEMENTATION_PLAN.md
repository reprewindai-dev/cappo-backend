# CAPPO ExecutionIdentityV1 Implementation Plan

## Status
This document assumes the following are verified against current code:

- `PGLCertificate` is the seed provenance object.
- `VeklomRunStateMachine` is the governing execution path.
- `MCPGateway` currently performs security checks but no proof-derived authority checks.
- `/v1/exec` bypasses the orchestrator.
- PGL simulation fallback can return non-persisted certificates.

## Objective
Implement `ExecutionIdentityV1` as the LAW 0 enforcement object that binds governance, proof, authority, scope, budget, and execution before any side-effecting action is allowed.

## Lineage anchors
ExecutionIdentityV1 is grounded in these code realities:

1. `PGLCertificate` in `backend/db/models/pgl.py`
2. `VeklomRunStateMachine` in `backend/services/orchestrator.py`
3. `MCPGateway` in `backend/core/security/mcp_gateway.py`

These are mandatory grounding references for all implementation work.

## First closure targets
The first implementation wave must close these LAW 0 violations:

1. `/v1/exec` bypass
2. missing execution-identity validation in `MCPGateway`
3. production acceptance of simulated or non-persisted PGL fallback certificates
4. any paid-path exec flow that checks only kill-switch state and not proof-derived authority

## Mint point
### Exact insertion point
The `ExecutionIdentityV1` mint must occur inside `backend/services/orchestrator.py` after `govern_run()` and after `commit_run()`, but before `route_run()`.

Required path:

`GOVERNED -> COMMITTED -> [mint ExecutionIdentityV1] -> ROUTED -> EXECUTING`

### Mint inputs
The mint operation must read:

- `run.pgl_identity["pre_execution_certificate_id"]`
- `run.seked_state`
- `run.v4_decision`
- the current run scope, budget, and delegation context
- canonical hashes already produced or referenced by the run

### Mint outputs
The mint operation must:

1. assemble the full `ExecutionIdentityV1` object
2. compute `hash` over canonical JSON
3. sign the object with the configured signing key
4. attach the object to `run.execution_identity`
5. persist the object in a dedicated `execution_identities` table

## ExecutionIdentityV1 fields
The implementation must populate these fields:

- `execution_id`
- `pgl_pre_certificate_id`
- `pgl_post_certificate_id` (optional)
- `genome_hash`
- `constitution_hash`
- `plan_hash`
- `tool_manifest_hash`
- `delegation_chain_hash`
- `input_hash`
- `seked_attestation_hash`
- `directive`
- `risk_tier`
- `budget_approved_cents`
- `budget_reserve_cents` (optional)
- `delegation_depth`
- `ttl_seconds`
- `expires_at`
- `scope`
- `human_attestation_hash`
- `ai_attestation_hash`
- `execution_attestation_hash`
- `issuer`
- `issued_at`
- `signature`
- `hash`

## Persistence model
### VeklomRun change
Add a new JSONB column:

- `execution_identity`

This allows the active run object to carry the minted identity through routing and execution.

### New table
Add a new table:

- `execution_identities`

Suggested columns:

- `execution_id` (primary key)
- `run_id`
- `workspace_id`
- `pgl_pre_certificate_id`
- `pgl_post_certificate_id`
- `directive`
- `risk_tier`
- `budget_approved_cents`
- `delegation_depth`
- `scope_json`
- `issued_at`
- `expires_at`
- `signature`
- `hash`
- `revoked` (boolean)
- `revoked_at` (nullable)
- `created_at`

## MCP Gateway validation contract
Add a new method to `MCPGateway`:

```python
@classmethod
def require_execution_identity(cls, execution_identity: dict):
    ...
```

This method must reject execution unless all validation rules pass.

### Validation rules
The gateway must validate all of the following:

1. `pgl_pre_certificate_id` resolves to a real, persisted PGL certificate.
2. `genome_hash`, `constitution_hash`, and `plan_hash` match the referenced PGL certificate.
3. the SEKED `directive` permits execution.
4. `expires_at` is in the future.
5. `scope` covers the requested tool and action.
6. `budget_approved_cents` is sufficient for the action cost.
7. `delegation_depth` is within the configured maximum.
8. `signature` and `hash` verify against the configured signing key.
9. the identity is not revoked.

### Enforcement scope
This validation must run before any side-effecting tool call from MCP execution paths.

It must also be reachable from any direct execution path that can cause side effects.

## Rejection behavior
When the execution identity is missing or invalid:

- return HTTP 403
- body format:

```json
{
  "error": "EXECUTION_IDENTITY_REQUIRED",
  "detail": "<specific reason>",
  "law0": true
}
```

- log the event to `AIAuditLog`
- set `operation_type="law0_violation"`
- never fall back to unguided or ungoverned execution

### Priority rule
If the kill-switch middleware would return HTTP 402, that response takes precedence over the LAW 0 rejection.

## /v1/exec migration
### Option A — Route through orchestrator
This is the preferred implementation.

Replace direct provider execution inside `/v1/exec` with a `RunOrchestrator` flow that:

1. creates a `VeklomRun`
2. governs the run
3. commits the run and mints the PGL pre-certificate
4. mints `ExecutionIdentityV1`
5. routes and executes through the governed path
6. attests the outcome
7. returns the result in the current response shape

Advantages:

- one enforcement path
- one mint point
- no drift between direct exec and orchestrated execution

### Option B — Independent EI gate on `/v1/exec`
This is acceptable only as a short-lived fallback if Option A is too disruptive for the first release.

Implementation:

- add a FastAPI dependency or middleware on `/v1/exec`
- require and validate `ExecutionIdentityV1` before handler execution
- keep rejection behavior identical to MCP Gateway

Risks:

- creates a second enforcement path
- raises long-term sync risk with orchestrator rules

## Production PGL rule
The production environment must forbid simulated fallback certificates.

Add this guard in `PGLClient.__init__()`:

```python
if os.getenv("CAPPO_REQUIRE_PERSISTENT_PGL", "").lower() == "true" and db is None:
    raise RuntimeError("PGL simulation fallback is forbidden in production. Provide a DB session.")
```

Production must set:

- `CAPPO_REQUIRE_PERSISTENT_PGL=true`

This ensures the non-persistent fallback path cannot be used in production.

## Rollout sequence
### Phase 1 — Schema and object model
- add `execution_identity` to `VeklomRun`
- create `execution_identities` table
- define canonical serialization, hashing, and signing helpers

### Phase 2 — Orchestrator minting
- mint `ExecutionIdentityV1` after `commit_run()` and before `route_run()`
- persist and attach identity to the run

### Phase 3 — Enforcement
- add `require_execution_identity()` to `MCPGateway`
- enforce validation on all side-effecting tool paths
- wire `/v1/exec` to orchestrator or temporary EI gate

### Phase 4 — Production hardening
- forbid non-persistent PGL fallback in production
- add revocation checks
- add structured LAW 0 audit logging and alerting

## Agent operating constraint
Any implementation agent must begin by re-reading:

- `CAPPO_GOLD_BLUEPRINT.md`
- `CAPPO_NAMING_AND_SCOPE.md`
- `AGENT_OPERATING_RULES.md`
- `CAPPO_EI_LINEAGE_AND_GAP_BRIEF.md`
- the verified code files for `pgl.py`, `orchestrator.py`, `mcp_gateway.py`, `exec_router.py`, `pgl_client.py`, and relevant middleware

If any contradiction is found, the agent must stop and report before changing code.
