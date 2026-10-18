# Devin Prompt — CAPPO Spec Phase

You are working on the CAPPO runtime track.

## Truth order
1. Human instructions in this task
2. `CAPPO_GOLD_BLUEPRINT.md`
3. `CAPPO_NAMING_AND_SCOPE.md`
4. Actual code and config
5. Other docs as context only

## Hard rules
- Do not edit docs unless explicitly instructed by the human doc owner.
- Do not touch servers.
- Do not deploy.
- Do not refactor unrelated code.
- Do not invent alternate architecture.
- Do not reuse old runtime names.

## First step: doc validation
Before doing any work:

1. Read `CAPPO_GOLD_BLUEPRINT.md`.
2. Read `CAPPO_NAMING_AND_SCOPE.md`.
3. Read `AGENT_OPERATING_RULES.md`.
4. Check whether the docs are still valid for the area you are about to work on.
5. If any doc is stale or contradicted by code, stop and report before proceeding.

## Current phase
Discovery is complete.

Current phase = ExecutionIdentityV1 specification.

No code yet unless explicitly assigned after spec approval.

## Task
Draft or refine the section:

`ExecutionIdentityV1 Specification — LAW 0 Enforcement Object`

The section must define only:

1. Fields
2. Lifecycle
3. Validation rules
4. Enforcement points

## Constraints
- Treat ExecutionIdentityV1 as the binding object, not a new platform.
- Base it on the proven runtime spine:
  - PGLCertificate
  - SEKED Decision
  - ExecutionIdentityV1
  - MCP Gateway
  - Runtime
  - DecisionFrame
  - Ledger
- Keep the first enforcement milestone limited to:
  1. closing `/v1/exec` bypass
  2. adding ExecutionIdentityV1 check to MCP Gateway
  3. disabling PGL simulation fallback in production

## Output
Produce draft text only for review by the human doc owner.
Do not commit docs unless explicitly instructed.
