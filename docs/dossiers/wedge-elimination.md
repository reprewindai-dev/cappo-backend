# Veklom Wedge Elimination Dossier

Date: 2026-09-09
Status: ACTIVE FALSIFIER PROGRAM
Purpose: reduce Veklom's differentiation claim to a narrow, reproducible hypothesis and attempt to kill it against real competing stacks.

## 1. Falsifiable wedge hypothesis

**Hypothesis W1:** A single bounded consequence contract can bind temporary non-widening authority, physical execution limits, durable ambiguity handling, and target-side reconciliation such that an authorized automation cannot silently widen authority, exceed its declared execution envelope, or blindly re-execute an uncertain consequence without generating machine-verifiable evidence.

This is intentionally narrower than claims such as "Veklom solves AI safety", "hyperscalers have a post-admission vacuum", or "competitors cannot reproduce Veklom". Those broader statements are not accepted unless the dossier produces direct comparative evidence.

### W1 decomposition

W1 passes only if all four subclaims pass on the same governed consequence path:

- **W1-A Authority:** authority is temporary, execution-bound, non-widening, and single-use where the lease contract requires it.
- **W1-B Physical bounds:** memory, compute/fuel, and wall-clock limits are enforced by the substrate rather than merely represented as policy metadata.
- **W1-C Ambiguity safety:** dispatch intent/consumption is durably recorded before the consequence boundary and uncertainty locks blind redispatch.
- **W1-D Finality evidence:** finality comes from a server-bound target-side observation/reconciliation path, not solely from the executor reporting success.

Failure of any subclaim means W1 is not proven as an integrated wedge.

## 2. Evidence admission rules

Accepted evidence:
- canonical code at an exact commit SHA;
- raw process/kernel/runtime output;
- on-disk receipts with measured timestamps;
- deterministic hostile tests with non-zero exit on drift;
- independent re-read/verifier output;
- competitor reproduction code and raw receipts.

Rejected as proof:
- architecture diagrams alone;
- generated completion banners;
- self-hosted simulators presented as production services;
- hard-coded timestamps/statuses;
- marketing copy;
- claims that a competitor "cannot" perform a function without an attempted reproduction.

Every result ends in `VALID`, `INVALID`, or `INDETERMINATE` with an evidence pointer.

## 3. Current Veklom evidence ledger

Do not reopen the defined PRODUCT-0 A-L battery. Use its accepted artifacts as lower-layer evidence, while separately proving that the same invariants are integrated into the canonical runtime path.

| Wedge subclaim | Existing proof surface | Integration state |
|---|---|---|
| W1-A authority / replay / substitution | PRODUCT-0 A, C, D, E, F, G dedicated proofs | canonical GitHub/runtime integration still requires branch-visible verification |
| W1-B physical bounds | Test H SIGKILL; Test I cgroup OOM; Test J Wasmtime fuel trap | proven at recorded substrate proof surfaces |
| W1-C ambiguity safety | Test K fsync WAL + OUTCOME_UNKNOWN lock | proven in dedicated harness; canonical live path must remain equivalent |
| W1-D finality | Test L reconciliation state machine plus target-observation work | generic independent target observer must be verified on canonical live path |

## 4. Competitor comparison rule: compare complete native stacks, not strawmen

The dossier MUST NOT compare Veklom's full stack to only one narrow competitor component.

Examples:
- SPIFFE is primarily workload identity / SVID issuance and validation; it is not a complete consequence-execution platform by itself.
- Amazon Verified Permissions / Cedar is an authorization component; Amazon Bedrock AgentCore also provides Policy/Gateway and isolated Runtime microVMs, so AWS must be tested as the relevant assembled native stack.
- Google Principal Access Boundary policies constrain resources principals are eligible to access and can apply to workload/agent principal sets; a fair Google reproduction must combine IAM/PAB with the relevant execution substrate and observability/retry mechanisms.
- Microsoft Entra Agent ID provides identity, authorization/governance, Conditional Access and audit capabilities; a fair Microsoft reproduction must include the execution/orchestration substrate used with it.

The claim to test is not "competitors are pre-admission only". The claim to test is whether their documented native stack can reproduce **W1-A through W1-D as one consequence contract with equivalent fail-closed semantics and evidence**.

## 5. Competitor kill-test protocol

For each competitor stack, build the smallest documented native implementation capable of attempting the same consequence.

### KT-1 Non-widening delegated authority

Attempt:
1. Issue the narrowest supported temporary authority for operation O on target T.
2. Delegate/derive/forward that authority using the competitor's supported mechanism.
3. Attempt to widen resource, operation, audience, lifetime, or target.
4. Record whether widening is cryptographically impossible, policy-denied at use time, or possible through a different credential path.

Pass for competitor reproduction: equivalent non-widening bound authority is demonstrated with raw evidence.

### KT-2 Physical execution envelope

Attempt:
1. Declare a memory ceiling, compute ceiling, and wall deadline before dispatch.
2. Run memory bomb, infinite compute loop, and wall-clock stall.
3. Capture kernel/hypervisor/runtime evidence showing the declared limits caused termination.
4. Verify no surviving supervised workload.

Do not claim uniqueness if the competitor runtime enforces equivalent physical bounds. Record the actual semantic difference instead.

### KT-3 Crash after consequence / response loss

Attempt:
1. Durably mark dispatch intent/authority consumption.
2. Let target commit consequence.
3. Drop response or kill orchestrator before success acknowledgement.
4. Restart orchestration.
5. Observe whether the stack blindly redispatches, relies on target idempotency, locks for reconciliation, or has another safe recovery mechanism.

The falsifier is a duplicate consequence or an automatic retry while outcome is genuinely unknown.

### KT-4 Target-side finality

Attempt:
1. Make executor report success while target state is not committed, or make response ambiguous after commit.
2. Require finality determination from an independently resolved target-side observer.
3. Block caller-supplied observer URLs or equivalent trust substitution.
4. Record whether finality evidence is independent of the executor's own return value.

### KT-5 Evidence integrity

Attempt:
1. Modify an execution/authority/receipt field after completion.
2. Run the independent verifier.
3. Determine whether tampering is detectable from the retained evidence chain.

Do not claim public transparency/E4 unless an external inclusion/anchor proof actually exists.

## 6. Initial competitor research corrections

These are research baselines, NOT completed reproduction results:

### AWS

Official AWS documentation describes AgentCore Policy as intercepting every Gateway tool call with Cedar policies and deny-by-default / forbid-wins semantics. AgentCore Runtime provides dedicated per-session microVMs with isolated CPU, memory, and filesystem, and destroys/sanitizes the microVM after session termination. Therefore the broad claim that AWS only performs pre-execution IAM and has no post-admission execution containment is **INVALID as stated**.

Open question for reproduction: can the AWS-native combination bind policy authorization, explicit resource envelope, single-use consequence authority, crash ambiguity lock, and independent target finality into an equivalent auditable consequence contract?

### Google

Principal Access Boundary policies limit the resources that a principal set is eligible to access, including workload identity and agent-identity principal sets. Therefore "Google tokens can simply widen" is not an accepted premise.

Open question for reproduction: what documented native Google stack provides equivalent execution-envelope commitment, consume-before-dispatch ambiguity handling, and target-side reconciliation semantics?

### Microsoft

Microsoft Entra Agent ID provides dedicated agent identities, authentication/authorization integration, Conditional Access, governance and audit logging. Therefore it should be evaluated as an identity/governance layer combined with the actual runtime/orchestration platform, not dismissed as a static identity token.

Open question for reproduction: can the Microsoft-native stack bind consequence authorization to physical resource bounds and ambiguity/finality semantics equivalent to W1?

### SPIFFE/SPIRE

SPIFFE standardizes workload identity, SVIDs, trust domains and a Workload API. It is an identity primitive and should be treated as a potential component inside either Veklom or a competitor construction, not as a full direct competitor to W1.

## 7. Friction test

"Frictionless" is not accepted as a qualitative adjective. Measure it.

For a clean external developer:
- time to first governed consequence;
- number of required configuration fields;
- number of manual approval steps;
- number of infrastructure components that must be installed;
- SDK/API calls required from mount/lease to consequence;
- failure-recovery steps after OUTCOME_UNKNOWN;
- whether raw target credentials are exposed to workload code.

Candidate threshold must be frozen before the external-user trial. Until then, "frictionless" remains `INDETERMINATE`.

## 8. Independent verification and sealing

Dossier closure requires:
1. exact Veklom commit SHA and CI/run identifiers;
2. canonical live runtime receipts, not simulator receipts;
3. independent verifier result for each admitted Veklom artifact;
4. competitor implementation source/config versions;
5. raw competitor test output;
6. a claim matrix marking each W1 subclaim `VALID`, `INVALID`, or `INDETERMINATE` for Veklom and each comparison stack;
7. cryptographic integrity protection for the dossier evidence set.

Cryptographic integrity is not automatically "immutable evidence". State the mechanism actually used (for example signed digest, append-only Merkle inclusion, external transparency inclusion) and its evidence tier. Do not claim E4/public transparency without the corresponding external proof.

## 9. Closure rule

The Wedge Elimination Dossier is CLOSED only if:

- Veklom W1-A through W1-D are verified on the intended canonical runtime path; AND
- at least one credible native reproduction attempt has been executed for each selected competitor stack; AND
- the final differentiation statement is narrowed to the capabilities the evidence actually distinguishes.

Possible outcomes are all useful:
- **UNIQUE WEDGE SUPPORTED** — tested competitor stacks fail one or more frozen W1 requirements under equivalent conditions.
- **COMPOSABLE BUT DIFFERENTIATED** — competitors can reproduce the guarantees, but only through materially different assembly/cost/friction/evidence semantics.
- **NO UNIQUE WEDGE** — a competitor reproduces W1 equivalently; Veklom must differentiate elsewhere.
- **INDETERMINATE** — insufficient direct competitor or canonical-runtime evidence.
