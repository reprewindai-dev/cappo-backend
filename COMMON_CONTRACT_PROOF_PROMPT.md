# Teamwork Project Prompt — Common-Contract Proof

**Status:** Launched
**Objective:** Prove the Veklom common execution contract
**Team shape:** Small focused team: one implementation owner followed by repeated independent adversarial review.

This is one architectural proof, not a broad refactor.

Do not optimize for feature count. Optimize for falsifiable evidence that persistent and ephemeral execution materialization can operate through the same governed capability contract without creating separate authority paths.

Working directory:

`C:\Users\antho\.windsurf\cappo-backend`

Integrity mode:

`benchmark`

---

# Architectural Objective

Prove this invariant:

> The execution substrate may change, but the authority and consequence contract does not.

A persistent execution substrate and an ephemeral execution substrate must both execute through the same canonical governed execution semantics:

request
→ verified execution context
→ authority resolution
→ effective-authority computation
→ consequence authorization
→ lifecycle policy selection
→ execution materialization
→ consequence
→ independent observation
→ evidence
→ lifecycle completion/dissolution

Persistent versus ephemeral describes **materialization policy**.

It must NOT create separate:

* authority models;
* authorization paths;
* evidence models;
* consequence semantics.

Authority remains bounded in both modes.

---

# R1. Canonical Capability Handler

Create:

`cappo_backend/services/capability_handler.py`

Extract the transport-independent governed execution semantics currently coupled to `/v1/exec`.

The HTTP router must become a transport adapter.

The router/middleware MAY perform operations that intrinsically require the raw HTTP request, including:

* parsing;
* HTTP message-integrity verification;
* transport authentication;
* conversion into a normalized verified execution context.

The router MUST NOT independently:

* resolve consequence authority;
* make the final authorization decision;
* select an ungoverned execution path;
* execute a consequence;
* declare consequence success;
* seal terminal consequence evidence independently.

After transport normalization, one canonical handler must own the governed semantic path.

Define or reuse an explicit normalized contract representing at minimum:

* authenticated principal/execution identity;
* request/intent commitment;
* workspace/realm;
* requested operation;
* target/resource;
* capability/authority reference;
* lifecycle policy;
* execution correlation identifiers.

Avoid creating parallel request semantics for persistent and ephemeral execution.

---

# R2. Common Contract / Dual Materialization Policies

Implement two execution materialization policies under the same capability contract:

1. **Persistent materialization**
2. **Ephemeral materialization**

They must share the same:

* authority wrapper;
* effective-authority computation;
* operation/resource binding;
* commitment semantics;
* execution correlation model;
* lifecycle evidence model;
* consequence-observation requirements.

The difference must be confined to materialization/lifecycle behavior.

Persistent materialization MUST NOT mean permanent authority.

Ephemeral materialization MUST expose evidence that the disposable execution instance was created and subsequently dissolved.

The resulting intended consequence and its evidence may remain durable after the ephemeral runtime disappears.

---

# R3. Real Consequence

Execute a real local consequence.

Use a safe deterministic consequence comparable to the existing Activation durable-target pattern.

Required chain:

real bounded authority
→ real governed execution
→ real state transition
→ durable consequence
→ independent consequence observation
→ evidence correlated to the original intent

Do NOT substitute:

* `test_only_echo`;
* HTTP status;
* an authorization receipt;
* an executor return value;
* fabricated test state

for the actual consequence.

SUCCESS may be emitted only after the target consequence has been independently re-observed.

---

# R4. Consequence-Dominance Boundary

Prove that the canonical handler is not merely a convention.

A consequence executor/target must require a handler-bound authorization artifact or equivalent non-forgeable execution authority sufficient to bind at least:

* execution ID;
* request/intent commitment;
* authorized operation;
* target/resource;
* authority/receipt reference;
* lifecycle execution context;
* expiry/freshness where applicable.

Arbitrary caller-supplied provenance fields must not become trusted simply because they contain the correct strings.

A direct attempt to create a new consequence without valid handler-bound execution authority must fail closed.

Reuse existing cryptographic primitives and authority structures where appropriate rather than inventing unnecessary parallel security systems.

---

# R5. Strict Trust Boundary

Mocks are permitted only inside non-security-relevant business behavior.

The proof MUST NOT mock or bypass:

* identity validity;
* capability validity;
* authority bounds;
* signature/integrity checks;
* expiry/freshness checks;
* revocation;
* replay protection;
* consequence authorization;
* real execution;
* durable consequence creation;
* independent observation;
* evidence correlation.

No test-only execution shortcut may participate in the proof path.

---

# R6. Lifecycle Evidence

For every execution, preserve correlation across:

* request/intent commitment;
* authority decision;
* capability/lease or equivalent authority reference;
* execution ID;
* materialization instance;
* consequence commitment;
* independent observation;
* terminal evidence.

For ephemeral execution additionally establish:

`MATERIALIZED → EXECUTING → CONSEQUENCE_ESTABLISHED → DISSOLVED`

or the existing equivalent lifecycle vocabulary.

Dissolution must not erase consequence or evidence continuity.

---

# Verification

Use the existing pytest suite and current adversarial fixtures wherever possible.

Extend existing tests rather than producing a disconnected demonstration harness unless architectural isolation requires otherwise.

Relevant existing surfaces include:

* `/v1/exec`;
* capability lease/mount validation;
* consequence lifecycle;
* Activation durable consequence;
* adversarial authority/replay tests;
* governed execution tests.

---

# Acceptance Criteria

## Canonical Contract

* [ ] `cappo_backend/services/capability_handler.py` exists.
* [ ] `/v1/exec` performs transport-specific verification/normalization and delegates governed execution semantics to the handler.
* [ ] There is one normalized execution/capability contract.
* [ ] Persistent and ephemeral materialization use that same contract.
* [ ] No second authority path is introduced for either materialization policy.

## Real Consequence

* [ ] A real durable state transition occurs.
* [ ] The consequence is independently re-observed.
* [ ] SUCCESS cannot be produced when independent observation fails.
* [ ] The consequence correlates to the original intent and authority.

## Materialization

* [ ] Persistent execution is demonstrated.
* [ ] Ephemeral execution is demonstrated.
* [ ] Ephemeral execution records materialization and dissolution.
* [ ] The consequence/evidence survives ephemeral-runtime dissolution.
* [ ] Changing materialization policy does not change governance semantics.

## Adversarial Dominance

* [ ] Direct creation of a NEW consequence without valid handler-bound execution authority is deterministically denied.
* [ ] Fabricated provenance fields do not confer authority.
* [ ] Revoked authority is denied.
* [ ] Expired/stale authority is denied.
* [ ] Scope/action widening is denied.
* [ ] Substitution of execution ID, intent, target, or authority binding is denied.

## Replay Semantics

* [ ] Replay using invalid, expired, revoked, or otherwise unusable authority is denied.
* [ ] Replay cannot create a second consequence.
* [ ] If exact execution replay is intentionally idempotent, it returns/re-observes the already committed consequence rather than executing it again.
* [ ] Conflicting reuse of an execution identifier fails closed.

## Evidence

* [ ] Request commitment → authority → execution → materialization → consequence → observation → terminal evidence is mechanically correlated.
* [ ] Tests verify the correlation rather than merely checking that fields exist.

---

# Required Proof Report

At completion, report separately:

### PROVEN

Behavior demonstrated by executed tests and independently observable state.

### IMPLEMENTED BUT NOT YET PROVEN

Code present without sufficient adversarial or independent verification.

### NOT PROVEN

Architecture outside the scope of this experiment.

Do not claim:

* kernel isolation;
* host-compromise resistance;
* hardware isolation;
* distributed/federated execution;
* production-scale scheduling;
* universal executor dominance

unless those properties were independently demonstrated in this experiment.

---

# Team Delegation

Keep implementation ownership singular.

## Implementer

Build the smallest coherent common-contract implementation and run the existing plus new proof suite.

## Adversarial Reviewer

After implementation, attempt to falsify:

* handler dominance;
* authority binding;
* persistent/ephemeral semantic equivalence;
* consequence uniqueness;
* observation independence;
* lifecycle dissolution;
* evidence continuity.

Return discovered holes to the implementer.

Repeat implement → attack → repair until the acceptance criteria are either proven or explicitly classified as not proven.
