# CAPPO ExecutionIdentityV1 Lineage and Gap Brief

## Confirmed lineage inputs
The CAPPO repository does not exist yet, so the work remains in doctrine, specification, and targeted implementation planning rather than new-repo execution.

ExecutionIdentityV1 must reference these lineage anchors:

### 1. PGLCertificate
Location: `backend/db/models/pgl.py:26-42`

Confirmed role:
- seed object
- contains `certificate_id`
- contains `genome_hash`
- contains `constitution_hash`
- contains `plan_hash`
- contains `output_hash`
- contains `outcome_hash`
- supports pre/post linking
- participates in hash-chained ledger events

### 2. VeklomRunStateMachine
Location: `backend/services/orchestrator.py:7-24`

Confirmed role:
- 14-state machine
- enforces the path `GOVERNED -> COMMITTED -> ROUTED -> EXECUTING`
- `commit_run()` at line 104 mints the PGL pre-certificate

### 3. MCPGateway
Location: `backend/core/security/mcp_gateway.py:33-66`

Confirmed current checks:
- injection scanning
- tool registry hash validation
- rate limiting
- egress allowlist
- file access blocking

Confirmed missing checks:
- no execution identity check
- no PGL certificate check
- no SEKED attestation check

## Confirmed LAW 0 violations
These three gaps are now confirmed and should be treated as the first CAPPO closure targets:

1. `/v1/exec` bypass exists and does not require a PGL certificate.
2. `MCPGateway` performs no ExecutionIdentityV1, PGL, or SEKED validation.
3. PGL simulation fallback can silently return certificates with `persisted: False`.

## What this changes in the blueprint
ExecutionIdentityV1 should now be specified as a lineage-bound enforcement object that inherits from proven runtime reality rather than as an abstract future layer.

That means the spec should explicitly bind to:
- PGLCertificate as the seed provenance object
- orchestrator commit as the pre-cert mint point
- MCP Gateway as the mandatory side-effect enforcement choke point

## Revised first implementation milestone
The first implementation milestone should be stated exactly as:

1. Close `/v1/exec` bypass so no direct side-effect path can execute without a valid enforcement object.
2. Add ExecutionIdentityV1 validation to `MCPGateway` before any side-effecting tool call proceeds.
3. Disable production acceptance of simulated or non-persisted PGL fallback certificates.

## Agent instruction update
Any implementation agent should now be instructed to treat the above three files as mandatory grounding references before proposing code changes.

Required startup sequence for the agent:
1. Read canonical CAPPO docs.
2. Re-read `pgl.py`, `orchestrator.py`, and `mcp_gateway.py`.
3. Confirm whether the docs still match those files.
4. Stop and report if any contradiction appears.
5. Only then draft code changes or a patch plan.

## Recommended next artifact
The next artifact should be an implementation-planning document, not code.

That document should define:
- the exact ExecutionIdentityV1 mint point
- the exact validation contract at MCP Gateway
- the rejection behavior for missing or invalid identity
- the migration path for `/v1/exec`
- the production rule that forbids simulated fallback acceptance
