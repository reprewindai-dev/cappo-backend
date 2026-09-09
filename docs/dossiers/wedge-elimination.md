# Veklom Wedge Elimination Dossier

Date: 2026-09-09
Status: ACTIVE FALSIFIER PROGRAM
Purpose: reduce Veklom's differentiation claim to a narrow, reproducible hypothesis and attempt to kill it against credible competing stacks.

> **Truth rule:** architecture and marketing language do not close this dossier. Only canonical code, measured execution, raw receipts, independent verification, and competitor reproduction attempts advance a claim.

---

# Executive Template

## Section 1 — The Falsifiable Wedge Hypothesis

### 1.1 Frictionless Flow Definition

**Hypothesis W1:** A single bounded consequence contract can bind temporary non-widening authority, physical execution limits, durable ambiguity handling, and target-side reconciliation such that an authorized automation cannot silently widen authority, exceed its declared execution envelope, or blindly re-execute an uncertain consequence without generating machine-verifiable evidence.

W1 decomposes into four independently falsifiable subclaims:

- **W1-A — Authority:** authority is temporary, execution-bound, non-widening, and single-use where the lease contract requires it.
- **W1-B — Physical bounds:** memory, compute/fuel, and wall-clock limits are enforced by the execution substrate, not merely represented as policy metadata.
- **W1-C — Ambiguity safety:** dispatch intent/consumption is durably recorded before the consequence boundary and uncertainty locks blind redispatch.
- **W1-D — Finality evidence:** finality comes from a server-bound target-side observation/reconciliation path, not solely from the executor reporting success.

W1 is not proven as an integrated wedge unless **all four subclaims pass on the same intended governed consequence path**.

### 1.2 Exclusion of Generic Marketing Claims

The dossier does **not** accept these as proven claims without direct evidence:

- "Veklom solves AI safety."
- "Veklom provides general governance."
- "Hyperscalers only perform pre-admission control."
- "Competitors cannot reproduce Veklom."
- "Veklom eliminates all post-admission indeterminacy."
- "Veklom provides exactly-once side effects."

The allowed claim surface is the narrower consequence-contract behavior actually supported by the evidence.

### 1.3 Boundary Scope

The target boundary is autonomous, programmatic execution in which:

1. authority cannot widen after issuance;
2. execution cannot exceed the declared physical envelope without substrate enforcement;
3. uncertain consequences cannot be blindly re-executed;
4. finality is established independently of executor self-report;
5. the control path does not require a human approval step for every bounded consequence.

"Frictionless" remains a **measurement target**, not an accepted adjective, until the developer-experience thresholds in Section 4 are frozen and tested.

---

## Section 2 — Empirical Proof Surface & Canonical Evidence Ledger

### 2.1 Empirical Code Mandate

Accepted evidence:

- canonical code at an exact commit SHA;
- raw kernel/process/runtime output;
- on-disk receipts with measured timestamps;
- deterministic hostile tests with non-zero exit on drift;
- independent re-read/verifier output;
- competitor reproduction code, configuration, and raw receipts.

Rejected as proof:

- architecture diagrams alone;
- white-paper claims without execution evidence;
- generated completion banners;
- self-hosted simulators represented as production services;
- hard-coded timestamps or expected-response servers;
- marketing copy;
- claims that a competitor "cannot" perform a function without an attempted reproduction.

Every admitted result ends in `VALID`, `INVALID`, or `INDETERMINATE` with an evidence pointer.

### 2.2 Physical Substrate Isolation Receipts

#### Memory Exhaustion — `ram_bomb`

Recorded PRODUCT-0 substrate evidence shows a cgroup v2 memory ceiling at 64 MiB with the kernel terminating the bounded workload under memory pressure. This is evidence for **W1-B physical memory enforcement at the recorded proof surface**.

Evidence to preserve in the final dossier:

- configured `memory.max`;
- peak memory observation;
- `oom_kill` / kernel event state;
- workload exit result;
- host survival;
- independent verifier result.

#### Compute Exhaustion — `spin_fuel`

Recorded PRODUCT-0 substrate evidence shows Wasmtime fuel exhaustion producing a deterministic WebAssembly fuel trap. This is evidence for **W1-B compute-bound enforcement at the recorded proof surface**.

Evidence to preserve:

- configured fuel budget;
- actual trap text/classification;
- absence of memory/deadline misclassification;
- independent verifier result.

#### Wall Deadline — Process-Group Teardown

Recorded Test H evidence uses subprocess/process-group isolation and OS-level `SIGKILL`, with the supervised child reaped (`rc=-9`). This proves the defined hard-deadline invariant. Do not reuse the older thread-abandonment proof as hard containment evidence.

### 2.3 PRODUCT-0 Closure Audit

The defined A-L PRODUCT-0 proof battery remains **CLOSED at its recorded proof surfaces**. The dossier consumes those lower-layer proofs; it does not reopen or renumber them.

| ID | Defined falsifier | Accepted status for dossier | Wedge relation |
|---|---|---|---|
| A | Unauthorized consequence | VALID at recorded proof surface | W1-A |
| B | Authorized bounded consequence | VALID at recorded proof surface | W1-A/B/D |
| C | Exact replay | VALID at recorded proof surface | W1-A/C |
| D | Authority mutation | VALID at recorded proof surface | W1-A |
| E | Lease / execution substitution | VALID at recorded proof surface | W1-A |
| F | Envelope substitution | VALID at recorded proof surface | W1-A/B |
| G | Execution revocation | VALID at recorded proof surface | W1-A |
| H | Hard deadline termination | VALID at recorded proof surface | W1-B |
| I | Memory violation / cgroup OOM | VALID at recorded proof surface | W1-B |
| J | Compute / fuel violation | VALID at recorded proof surface | W1-B |
| K | Crash/restart uncertainty + WAL lock | VALID at recorded proof surface | W1-C |
| L | Reconciliation unavailable + authority lock | VALID at recorded proof surface | W1-C/D |

### 2.4 Canonical Integration State

Lower-layer proof closure is distinct from repository-canonical production integration.

| Wedge subclaim | Existing proof surface | Canonical integration status |
|---|---|---|
| W1-A authority / replay / substitution | A, C, D, E, F, G | branch-visible production integration still under PR verification |
| W1-B physical bounds | H, I, J | proven at recorded substrate proof surfaces |
| W1-C ambiguity safety | K | proven in dedicated harness; canonical path must remain equivalent |
| W1-D finality | L plus target-observation work | generic independent target observer still requires canonical live-path verification |

---

## Section 3 — Competitor Kill-Test Matrix & Reproduction Protocols

### 3.1 Evaluation Vector 1 — Post-Admission Consequence Control

Do **not** assume a competitor relinquishes control after authorization. Test it.

**Question:** after access is granted and the consequence is dispatched, can the native competitor stack enforce equivalent ambiguity, retry, and finality semantics?

Selected candidate stacks may include:

- AWS native authorization + execution stack;
- Google Cloud IAM/PAB + execution/orchestration stack;
- Microsoft Entra Agent ID + execution/orchestration stack;
- Permit.io / Cerbos combined with a credible execution substrate;
- SPIFFE/SPIRE only as an identity component inside a larger reproduction, not as a full direct execution-stack competitor.

**Falsifier:** if a competitor stack reproduces W1-C and W1-D equivalently under the frozen conditions, the "post-admission vacuum" differentiation claim fails for that stack.

### 3.2 Evaluation Vector 2 — Non-Widening Authority & Cryptographic Binding

**Attempt:**

1. issue the narrowest supported temporary authority for operation O on target T;
2. derive/delegate/forward it using the competitor's supported mechanism;
3. attempt to widen resource, operation, audience, lifetime, execution, or target;
4. attempt execution-context substitution and envelope substitution;
5. record whether widening is cryptographically impossible, policy-denied at use time, or possible through an alternate credential path.

**Veklom reference behavior:** execution/context drift and envelope drift are expected to fail closed at the relevant proof surfaces (`ExecutionIdMismatchError`, `EnvelopeSubstitutionError`).

**Competitor reproduction PASS:** equivalent non-widening bound authority is demonstrated with raw evidence.

### 3.3 Evaluation Vector 3 — Substrate Teardown & Hard Process Isolation

**Attempt:**

1. freeze memory, compute/fuel, and wall-clock bounds before dispatch;
2. run a memory bomb;
3. run an infinite compute loop;
4. run a wall-clock stall;
5. capture kernel/hypervisor/runtime evidence;
6. verify no supervised workload survives the deadline path.

**Veklom reference behavior:** cgroup OOM enforcement, Wasmtime fuel trap, process-group `SIGKILL`/reaping at the defined proof surfaces.

Do **not** claim uniqueness if a competitor enforces equivalent physical bounds. Record the actual difference in binding, lifecycle, evidence, cost, or operational composition.

### 3.4 Evaluation Vector 4 — Crash Ambiguity & Target Finality

**Attempt:**

1. durably mark dispatch intent / authority consumption;
2. let the target commit the consequence;
3. drop the response or kill the orchestrator before success acknowledgement;
4. restart orchestration;
5. determine whether the system blindly redispatches, relies on target idempotency, locks for reconciliation, or has another safe recovery mechanism;
6. require finality to come from a server-resolved target-side observation path;
7. attempt caller-supplied observer substitution.

**Veklom reference behavior:** synchronous WAL persistence before dispatch, `OUTCOME_UNKNOWN` ambiguity lock, no blind retry, and reconciliation-driven finality at the recorded proof surfaces.

**Falsifier:** duplicate consequence, automatic redispatch under genuine uncertainty, or executor-self-report accepted as finality when independent observation is required.

### 3.5 Evaluation Vector 5 — Evidence Integrity

**Attempt:**

1. alter authority, execution, envelope, or receipt data after completion;
2. rerun the independent verifier;
3. record whether tampering is detected;
4. record the exact evidence tier achieved.

Do not call a hashed archive "immutable" without naming the actual protection mechanism. A signed digest, append-only Merkle inclusion, and external transparency inclusion are different evidence strengths.

### 3.6 Competitor Kill-Test Result Matrix

The final dossier must contain a table of this form, populated only from real reproductions:

| Stack | W1-A Authority | W1-B Physical Bounds | W1-C Ambiguity Safety | W1-D Finality Evidence | Overall |
|---|---|---|---|---|---|
| Veklom canonical target path | PENDING integrated verification | VALID at substrate proof surfaces | VALID at dedicated proof surface | PENDING generic canonical observer verification | INDETERMINATE |
| AWS native stack | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE |
| Google native stack | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE |
| Microsoft native stack | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE |
| Permit.io / Cerbos + execution substrate | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE | INDETERMINATE |

No competitor row may be upgraded from `INDETERMINATE` using documentation alone.

---

## Section 4 — Frictionless Adoption & Developer Experience Audit

### 4.1 Contract Standard Compliance

Target integration surface:

- OpenAPI 3.1 contract for mount/dispatch/reconciliation;
- AsyncAPI 3.0 event contract where events are actually emitted;
- repository-canonical contract files on the same commit as the runtime under test.

Current rule: do not claim machine-enforced contract compliance until the normative contracts, runner, runtime, and evidence are visible and passing on the same GitHub commit/CI run.

### 4.2 Ingress & Custody Hardening

Verify on the canonical runtime path:

- client-supplied `Veklom-Authority` is not accepted as machine authority;
- browser-facing responses contain no raw Biscuit token/private key material;
- authority minting/issuance occurs only through the canonical trusted server path;
- caller-supplied reconciliation URLs cannot replace server-bound observer resolution.

### 4.3 Client SDK Conformance

The Python and TypeScript SDKs are currently **provisional contract-aligned stubs**.

Required SDK checks:

- 401/403 -> authority denial mapping;
- 422 -> execution/envelope substitution mapping;
- 423 -> replay / retry-lock / authority-lock mapping according to the frozen contract;
- 503 -> reconciliation infrastructure unavailable;
- no client API for manufacturing `Veklom-Authority`;
- response models exactly match the normative OpenAPI on the same commit.

Do not label the SDKs mechanically generated from OpenAPI until that generation path is itself reproducible.

### 4.4 Measured Friction Audit

Freeze thresholds before the external-user test. At minimum measure:

- time to first governed consequence;
- number of configuration fields;
- number of manual approval steps;
- number of infrastructure components installed;
- API/SDK calls required from authority issuance to finality;
- recovery steps after `OUTCOME_UNKNOWN`;
- whether raw target credentials reach workload code.

Until those thresholds are frozen and measured, **"frictionless" = INDETERMINATE**.

---

## Section 5 — Independent Verification & Dossier Sealing

### 5.1 Independent Verifier Verification

Dossier closure requires reproducible verification of:

- PRODUCT-0 A-L lower-layer evidence;
- static contract conformance runner;
- canonical live runtime hostile battery using a **client-only** runner against an independently started CAPPO service;
- exact contract-vs-response schema/status validation;
- competitor reproduction outputs.

The current client-only runtime runner in this branch must not be confused with Notebook's earlier self-hosted `BaseHTTPRequestHandler` simulation.

### 5.2 Evidence Integrity Sealing

For every admitted run, seal:

- raw request/response JSON;
- measured timestamps;
- commit SHA;
- branch/ref;
- CI run/job identifiers where applicable;
- verifier result;
- digest/signature or stronger integrity mechanism actually used.

Hashing alone proves integrity relative to the retained digest; it does **not** by itself prove append-only immutability or public transparency.

### 5.3 Release / Tag Sign-Off

Do **not** predeclare `v0.9.0-PRODUCT-0-RC1` as the dossier seal.

A release/tag is accepted only if:

1. the tag exists in GitHub;
2. it points to the exact audited commit;
3. required CI/runtime evidence exists for that commit;
4. no simulated-runtime receipts are represented as canonical live evidence;
5. the dossier claim matrix is frozen with explicit `VALID` / `INVALID` / `INDETERMINATE` outcomes.

---

# Closure Rule

The Wedge Elimination Dossier is CLOSED only if:

1. Veklom W1-A through W1-D are verified on the intended canonical runtime path;
2. at least one credible native reproduction attempt is executed for each selected competitor stack;
3. the developer-friction threshold is frozen and measured;
4. the evidence set is integrity-protected with the mechanism explicitly identified;
5. the final differentiation statement is narrowed to what the evidence actually distinguishes.

Possible outcomes:

- **UNIQUE WEDGE SUPPORTED** — tested competitor stacks fail one or more frozen W1 requirements under equivalent conditions.
- **COMPOSABLE BUT DIFFERENTIATED** — competitors reproduce the guarantees only through materially different assembly, cost, friction, or evidence semantics.
- **NO UNIQUE WEDGE** — a competitor reproduces W1 equivalently; differentiation must move elsewhere.
- **INDETERMINATE** — insufficient canonical-runtime, friction, or competitor reproduction evidence.
