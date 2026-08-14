# Veklom Runtime Authority and Provider Routing Design

**Status:** Approved design baseline

**Date:** 2026-08-13

**Scope:** `cappo-backend` runtime only; no frontend work

## Objective

Make `POST /v1/exec` Veklom's sole public consequence-bearing execution
entrypoint. Provider federation and failover operate internally and only after
CAPPO has authorized the consequence. Routing may change execution location;
it may never create, widen, reinterpret, or bypass authority.

This design replaces the prototype semantics represented by
`POST /api/fpi/execute`. Provider registration, health, and read-only discovery
may remain federation-plane APIs. Capacity reservation, paid allocation, and
settlement mutation are consequential operations and must execute through the
governed capability path or remain internal.

## Non-Negotiable Invariant

```text
CAPPO decides whether a consequence may occur.
FPI/HRMR decides where that already-authorized consequence may occur.
```

`/v1/exec` is the only public execution authority boundary. There is no
redirect or schema adapter from `/api/fpi/execute`; the old route is removed
from public ingress and code. If a migration response is temporarily required,
it returns a non-redirecting deprecation response and performs no execution.

Multiple execution runtimes may remain live concurrently. Concurrency does
not grant shared ownership: exactly one runtime instance owns a given
consequence-bearing execution path for a given authority epoch. A runtime
change is an explicit authority-side reassignment with a new `assignment_id`
and a strictly greater `authority_epoch`; the previous assignment remains
durable history. A runtime that cannot prove the current assignment fails
stop before any side effect.

## Components

### Public execution boundary

`POST /v1/exec` owns:

1. request validation;
2. identity and delegation validation;
3. idempotency and replay admission;
4. capability resolution;
5. policy and budget binding;
6. CAPPO final semantic authorization;
7. construction of the immutable authorized execution envelope;
8. invocation of the internal provider router;
9. durable evidence and settlement orchestration;
10. the final signed HTTP response.

### CAPPO

CAPPO produces either a terminal denial or an authorized execution envelope.
It is the only component allowed to authorize a consequence. A denial cannot
be converted into provider unavailability, retried against another policy, or
sent to FPI for reinterpretation.

### Internal FPI/HRMR provider router

The router is a Python service, not a public consequence-bearing endpoint. Its
interface is conceptually:

```python
execute_authorized(envelope: AuthorizedExecutionEnvelope) -> ExecutionOutcome
```

It may:

- validate the envelope's integrity, freshness, and provider audience;
- filter only within `allowed_provider_set`;
- rank eligible providers using health, locality, cost, carbon, latency, and
  capacity signals allowed by policy;
- create a new `attempt_id` for each provider call;
- fail over after a verified movable failure;
- emit attempt evidence.

It may not:

- create a grant;
- change `grant_id`, `authority_epoch`, `policy_digest`, capability, actor,
  delegation, resource constraints, or budget ceiling;
- add a provider to `allowed_provider_set`;
- treat CAPPO denial as retryable;
- silently downgrade required evidence, signatures, sovereignty, or payment;
- declare execution or settlement without verified runtime evidence.

### Provider adapter

Each provider adapter performs a real provider call. It transmits the same
authorized semantic envelope with provider-specific binding and a unique
attempt identifier. It returns a typed outcome that distinguishes verified
HTTP semantics from transport failures and unverifiable responses.

### Evidence recorder

PGL records the request, CAPPO decision, routing decisions, provider attempts,
result, measurement, and settlement under a single `execution_id`. Evidence
failure is surfaced honestly; no synthetic receipt or transaction hash is
accepted as proof.

## Authorized Execution Envelope

The envelope is immutable after CAPPO authorization and contains at minimum:

```text
execution_id
path_id
request_id
idempotency_key
grant_id
subject_id
delegation_id
tenant_id
workspace_id
capability_id
semantic_intent_digest
resource_constraints
authority_epoch
assignment_id
runtime_kind
runtime_instance
policy_digest
allowed_provider_set
budget_ceiling
evidence_profile
issued_at
expires_at
nonce
```

The implementation uses a strict typed model that rejects missing fields and
unknown authority-bearing fields. The router receives this model, not an
unvalidated dictionary.

The following remain constant for the entire semantic transaction:

```text
execution_id
path_id
grant_id
authority_epoch
assignment_id
runtime_kind and runtime_instance
policy_digest
capability_id
subject and delegation
resource constraints
budget ceiling
allowed_provider_set
```

The following change per provider attempt:

```text
attempt_id
provider_id
executor_binding
attempt timestamps
provider response evidence
```

Provider failover does not reassign runtime ownership. A provider and a
runtime are different layers: provider attempts may change inside the same
runtime assignment, while moving the path to another runtime requires a new
authority epoch and assignment.

## HTTP Integrity Profile

Consequence-bearing requests and provider responses follow the Veklom HTTP
profile built on RFC 9421 and RFC 9530.

Request verification covers at least:

```text
@method
@authority
@path
content-type
content-digest
idempotency-key
veklom-execution-id
veklom-grant-id
veklom-authority-epoch
veklom-policy-digest
```

Provider response verification covers at least:

```text
@status
content-type
content-digest
veklom-execution-id
veklom-attempt-id
veklom-provider-id
veklom-authority-epoch
veklom-policy-digest
```

The signature profile requires `keyid`, `created`, `expires`, and nonce/replay
handling. Keys are obtained from the configured production key system; there
is no hard-coded HMAC fallback. A digest or signature proves integrity and
authenticity for covered components, not authorization or correctness of
computation.

## Execution Flow

```text
POST /v1/exec
  -> validate request, identity, delegation, replay and preconditions
  -> resolve capability and bind budget/policy
  -> CAPPO final authorization
       -> deny: return signed 403; record denial; stop
       -> allow: construct immutable authorized envelope
  -> internal FPI/HRMR filters allowed_provider_set
  -> select Provider A
  -> create attempt A
  -> call Provider A
       -> verified success: record and return result
       -> verified movable 503: record attempt A and revalidate envelope
       -> terminal 403: record and stop
       -> invalid/missing signature: record verification failure and stop
  -> select still-eligible Provider B from original allowed_provider_set
  -> create attempt B under the same execution_id
  -> call Provider B
  -> seal outcome, measurement, and settlement evidence
  -> return signed response
```

No execution-oriented provider discovery occurs before CAPPO authorization.
CAPPO denial causes zero provider calls.

## Failure and Degradation Rules

### CAPPO denial

```text
CAPPO 403
-> no FPI execution call
-> no provider selection
-> zero provider attempts
-> denial evidence sealed
-> signed terminal 403 returned
```

### Provider authority denial

A verified provider `403` is terminal. It cannot trigger failover. The attempt
and denial are recorded under the current execution.

### Provider unavailability

Only a response whose `503` status and required envelope identifiers pass the
HTTP signature profile is a semantically verified movable `503`. Failover then
uses the unchanged envelope and only another provider in the original allowed
set.

A network timeout, connection refusal, or unverifiable `503` is not silently
treated as an authenticated provider statement. Policy must explicitly define
whether transport failure is movable. The production default is fail closed;
any enabled transport-failure failover is separately labeled and recorded as
transport evidence, never as a signed provider `503`.

### Envelope mutation

Any change to authority-bearing fields between attempts terminates execution,
records an integrity violation, and prevents another provider call.

### Runtime ownership conflict

A missing assignment, stale epoch, mismatched runtime instance, or silent
owner replacement terminates the path before execution. The runtime reports a
non-retryable ownership conflict. It never guesses a replacement owner and
never derives a new epoch locally.

### Evidence failure

If the required evidence profile cannot durably record the decision or attempt,
the runtime fails closed before the next consequence. It does not manufacture a
receipt or report the result as fully settled.

### Payment and allocation

Payment proves economic admission, not authority. Capacity reservation or paid
allocation is invoked as a governed capability through `/v1/exec` or remains an
internal step after authorization. Settlement mutation requires evidence of the
actual execution outcome.

## Public Federation APIs

Allowed public federation-plane operations may include authenticated:

- provider registration;
- provider heartbeat/status submission;
- provider/capability discovery where disclosure policy permits;
- scoped read-only billing and evidence queries.

The following are not independent public mutation paths:

- provider execution;
- paid resource allocation;
- settlement mutation;
- authority creation or expansion.

## Persistence

Prototype module-level dictionaries are not permitted. Production state uses
the configured database with transactional records for:

- provider registrations and health observations;
- authorized executions;
- provider attempts;
- replay/idempotency claims;
- leases and monotonic fencing state;
- evidence references;
- settlement state.

Lease expiration is enforced by both request-time validation and a runtime
expiry/reconciliation worker. Fencing tokens come from authoritative monotonic
storage, not wall-clock timestamps.

## Required Proof Suite

### DENY

Given a request CAPPO denies:

- `/v1/exec` returns `403`;
- the internal provider router is not invoked;
- Provider A and Provider B receive zero calls;
- the denial has one execution identity and durable evidence.

### FAILOVER

Given an envelope authorizing providers A and B:

- Provider A receives attempt A;
- A returns a correctly signed `503` bound to execution and attempt A;
- Provider B receives attempt B;
- B succeeds;
- both attempts share the same `execution_id`, grant, authority epoch, policy
  digest, capability, actor/delegation, constraints, and budget;
- attempt and provider identifiers differ;
- the complete chain is durable evidence.

### Negative protocol proofs

Tests also prove:

- an unsigned or incorrectly signed `503` does not trigger semantic failover;
- a provider `403` never touches the next provider;
- an altered `@status`, body, execution identifier, attempt identifier,
  authority epoch, or policy digest fails verification;
- an expired envelope fails before provider contact;
- a provider outside `allowed_provider_set` cannot be selected;
- a replayed nonce or conflicting idempotency payload fails;
- evidence failure prevents continued consequential execution;
- `/api/fpi/execute` cannot execute publicly;
- allocation and settlement mutations cannot bypass `/v1/exec` governance.

Tests use deterministic local HTTP test servers or transports. They do not use
seeded provider success, randomized latency, synthetic settlement, or arbitrary
`pgl_` signature acceptance.

## Migration

1. Preserve the existing `/v1/exec` contract while adding strict internal
   models and protocol proof tests.
2. Extract provider routing from the current resilient executor into the
   internal authorized-envelope service.
3. Add RFC 9421/RFC 9530 response verification before movable failover.
4. Persist executions and attempts under the existing governed run/evidence
   model or a normalized extension of it.
5. Remove registration of `/api/fpi/execute`; do not redirect it.
6. Keep or rebuild non-consequential FPI federation APIs only after replacing
   in-memory state and applying authentication, validation, and disclosure
   policy.
7. Port useful schema and discovery concepts from recovery artifacts only after
   they conform to this contract.
8. Deploy exclusively as Docker workloads through Coolify and verify live
   behavior before applying `VERIFIED_LIVE` labels.

## Constraints

- No frontend changes.
- No Vercel deployment or configuration.
- No mock, seeded, or random production behavior.
- No hard-coded secrets or permissive signature fallbacks.
- No public execution route besides `/v1/exec`.
- No provider call before CAPPO authorization.
- No failover that changes authority-bearing fields.
- No production claims without repository tests and live Coolify evidence.

## Acceptance Criteria

The runtime is ready for subsequent FPI capability porting only when:

1. the DENY and FAILOVER proofs pass through `/v1/exec`;
2. signed response semantics are verified before failover;
3. executions and attempts are reconstructable from durable evidence;
4. the prototype public execution route is absent;
5. no user-facing or runtime path reports simulated providers, receipts,
   settlement, or attestation as production fact;
6. the complete backend test suite, static checks, Docker build, and Coolify
   deployment verification pass.
