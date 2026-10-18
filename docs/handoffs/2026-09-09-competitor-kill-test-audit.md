# Notebook Competitor Kill-Test Audit — Antigravity Handoff

Date: 2026-09-09
Classification: NOTEBOOK SIMULATION AUDIT
Branch: `product0/notebook-contract-handoff-20260909`
PR: #99

## Executive verdict

Notebook's uploaded `run_competitor_kill_tests.py` and `competitor_kill_test_results.json` are **not competitor reproduction evidence**. They are a simulation/report generator that hard-codes assumed competitor failures and then emits `PASS` / `UNIQUE_WEDGE_SUPPORTED` without invoking AWS, Google, Microsoft, Kubernetes, Permit.io, Cerbos, CloudWatch, Cloud Logging, or any provider-specific API/runtime.

The uploaded final dossier verdict `UNIQUE WEDGE SUPPORTED` is therefore **INVALID as an evidence-backed conclusion**.

The correct current dossier outcome remains **INDETERMINATE** pending:

1. canonical Veklom W1-A through W1-D integration proof on the intended runtime path;
2. real friction benchmark execution against a live runtime (Notebook's prior 7/7 friction result was false-green and has already been rejected);
3. fair provider-specific competitor reproductions with raw evidence.

## Why the Notebook competitor script is not a reproduction

### KILL-01

Notebook sets:

- `competitor_kill_01_result = "FAILED_GAP"`
- `veklom_kill_01_result = "PASS_PROTECTED"`

and writes narrative strings about AWS AgentCore/Cedar and Google PAB/SPIFFE. No AWS or Google API is called, no token is minted, no policy is deployed, and no substitution attempt is sent to a provider runtime.

**Status:** SIMULATED / UNSUPPORTED as competitor evidence.

### KILL-02

Notebook sets `competitor_kill_02_result = "FAILED_GAP"` and assumes competing orchestrators rely on `thread.join()` abandonment. This is not a valid characterization of modern container/microVM runtimes and no competitor process tree is created or inspected.

**Status:** SIMULATED / UNSUPPORTED as competitor evidence.

### KILL-03

Notebook asserts AWS Bedrock AgentCore / Google Agent Gateway will perform unmanaged retries after a dropped response and cause duplicate consequences. No provider workload, target, network fault, retry policy, or duplicate consequence is executed or observed.

**Status:** SIMULATED / UNSUPPORTED as competitor evidence.

### KILL-04

Notebook asserts Azure Confidential Compute / Entra Agent ID treats local exit code 0 as target finality. No Azure/Entra workload or orchestration stack is executed and Entra Agent ID is primarily an identity/access-governance layer, not by itself a target-finality orchestrator.

**Status:** SIMULATED / UNSUPPORTED as competitor evidence.

### KILL-05

This is the only part that actually computes something: it hashes two different local byte strings and observes that SHA-256 values differ. That proves only that different bytes produce different hashes in this fixture. It does **not** prove Veklom Merkle verification, CloudWatch/Cloud Logging mutability, or provider evidence weakness.

**Status:** VALID only as a trivial local SHA-256 mutation demonstration; INVALID as competitor reproduction or Veklom Merkle proof.

## External documentation cross-check

Current official provider documentation further falsifies several broad assumptions in the Notebook narrative:

- AWS AgentCore Runtime documents dedicated per-session microVMs with isolated CPU, memory, and filesystem, followed by microVM termination/sanitization. AgentCore Policy can intercept/evaluate gateway tool calls with Cedar/Dogwood enforcement. Therefore "AWS lacks post-admission containment" is not an acceptable premise.
- Google Principal Access Boundary policies constrain resources that principal sets are eligible to access, including workload identity pools and certain agent-identity principal sets. Therefore token-widening behavior must be tested, not assumed.
- Microsoft Entra Agent ID provides agent identities, authorization/governance, Conditional Access, lifecycle controls, and audit. It must be paired with the actual execution/orchestration substrate for a fair W1 comparison; it cannot be treated as an orchestrator that simply trusts exit code 0.

These documentation facts do **not** prove the competitors reproduce W1. They only show why direct provider reproductions are required.

## Canonical replacement now landed

`scripts/run_competitor_kill_tests.py` on this branch is now an **evidence-gated verifier**. It:

- never simulates provider behavior;
- requires a real `competitor_reproduction_manifest.json`;
- requires provider/service version, execution timestamp, reproduction command/procedure, and raw evidence files;
- hashes admitted evidence files;
- forces incomplete tests to `INDETERMINATE`;
- never emits `UNIQUE_WEDGE_SUPPORTED` by itself;
- exits non-zero while evidence is incomplete.

## Antigravity work only

Do **not** reproduce Notebook's simulated 5/5 battery.

Build provider-specific reproduction adapters / procedures for the frozen KILL-01..KILL-05 vectors and return raw evidence.

Minimum required return per provider/test:

- provider + exact service/product combination;
- region/account/project/subscription context where material;
- provider/API/runtime version or dated surface;
- exact configuration/policy files;
- exact commands/API calls used;
- measured start/end timestamp;
- raw stdout/stderr / HTTP / audit logs / runtime receipts;
- target-side state where the test concerns consequence finality;
- result: `VALID`, `INVALID`, or `INDETERMINATE` relative to the frozen competitor reproduction criterion;
- evidence file paths and hashes;
- branch/commit SHA containing the reproduction harness/config.

## Claim discipline

- PRODUCT-0 A-L lower-layer proof battery: **CLOSED at recorded proof surfaces**.
- Veklom integrated W1 canonical runtime path: **INDETERMINATE pending remaining production integration/runtime proof**.
- Notebook friction 7/7 claim: **INVALID** (false-green benchmark already audited).
- Notebook competitor 5/5 claim: **INVALID** as reproduction evidence.
- `UNIQUE WEDGE SUPPORTED`: **NOT PROVEN**.
- Current Wedge Dossier verdict: **INDETERMINATE**.

Only upgrade the final dossier after real provider reproductions and the canonical Veklom W1 path are both evidenced.
