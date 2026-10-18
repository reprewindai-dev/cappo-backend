# CLOUD-CONTROL-PLANE-1 - Forward-Scout Specification

**Status:** `DESIGN_VALID / NOT YET MEASURED`  
**Scope:** AWS / GCP / Azure infrastructure consequence adapters  
**Purpose:** Define the stable Veklom contract before any provider-specific implementation is treated as proof.

## 1. Doctrinal Boundary

Cloud IAM authorizes provider API access. Veklom must additionally preserve the exact consequence contract through dispatch uncertainty, asynchronous provider operations, authoritative state re-observation, retry fencing, and evidence preservation.

The stable chain is:

```text
CapabilityLease
  -> CanonicalCloudMutation
  -> provider-specific mapping
  -> provider dispatch
  -> possible timeout / response loss
  -> OUTCOME_UNKNOWN when finality is not established
  -> provider operation observation
  -> authoritative resource-state re-read
  -> RECONCILED_SUCCEEDED / RECONCILED_FAILED / RECONCILIATION_UNAVAILABLE
  -> durable evidence
```

Provider audit logs are supporting evidence. They do not automatically prove final resource state.

## 2. Canonical Cloud Mutation Contract

A provider adapter MUST consume a provider-neutral request such as:

```text
transition_id
request_commitment
authority_digest
execution_id
attempt_id
provider
account_project_subscription
region_or_scope
resource_type
resource_identity
action
semantic_version
payload_digest
expected_precondition
expected_postcondition
```

Provider-specific realization MUST bind:

```text
provider_api
provider_api_version
provider_operation_name
provider_request_digest
provider_request_id
provider_operation_id
provider_idempotency_token (when the specific API supports one)
mapping_contract_digest
```

A change in provider/API semantics that changes the canonical effect requires a new semantic binding and fresh authority decision.

## 3. Idempotency Rule

There is no universal idempotency-token field across AWS, GCP, and Azure, nor across all actions within one provider.

Therefore:

```text
provider native idempotency
    = optional defense in depth
    != Veklom consequence finality
```

If the selected provider action supports an idempotency token/request ID, derive or bind it deterministically to the Veklom transition and preserve it in evidence. If the provider action does not support such a mechanism, the adapter MUST NOT invent one or claim exactly-once behavior.

A retry is allowed only when the Veklom state machine establishes it is safe. A new retry attempt receives a new `attempt_id`; it does not silently create a new authority grant.

## 4. Finality Hierarchy

Finality evidence should be evaluated in this order:

1. **Provider operation state** - if the API exposes an asynchronous operation/request object, inspect it using the provider-issued operation identifier.
2. **Authoritative resource-state readback** - query the actual resource/configuration and verify the canonical expected postcondition.
3. **Provider audit log** - use CloudTrail, Cloud Audit Logs, Azure Activity/diagnostic logs, or equivalent as corroborating provenance and dispatch evidence.
4. **Client HTTP response** - useful transport evidence, never sufficient alone after an ambiguous dispatch.

The observer MUST be independent of the executor's success narration.

## 5. State Semantics

```text
AUTHORIZED
  -> DISPATCHING
  -> CONFIRMED_FAILED            if provider definitively rejects before consequence
  -> OUTCOME_UNKNOWN             if dispatch may have crossed the consequence boundary
  -> RECONCILED_SUCCEEDED        if authoritative provider state matches the contract
  -> RECONCILED_FAILED           if authoritative provider state proves the consequence did not occur
  -> RECONCILIATION_UNAVAILABLE  if authoritative observation cannot currently be completed
```

`RECONCILIATION_UNAVAILABLE` keeps authority fenced. Subsequent redispatch while fenced returns the locked-state response; it does not widen to another provider or another resource.

## 6. Cross-Provider Semantic Isolation

The adapter may translate the canonical consequence into provider-native mechanics, but it may not:

- add broader resource scope;
- add actions not represented by the canonical mutation;
- substitute another account/project/subscription;
- silently change region/scope;
- substitute another resource ID;
- change API semantics/version without rebinding;
- select a fallback provider after authorization unless that provider was already inside the authorized candidate set and the realization is rebound before dispatch.

The provider-native request is valid only if its policy-relevant semantic projection is proven to be within the canonical commitment.

## 7. Minimum First Implementation

Do not implement AWS, GCP, and Azure simultaneously.

Choose one provider and one narrow, reversible or low-risk control-plane action with:

- a documented authoritative readback API;
- a documented operation/request identifier;
- deterministic pre/postconditions;
- a locally controllable timeout/fault-injection seam;
- no financial or production blast radius.

Prove the generic contract against that one action before adding a second provider.

## 8. Seal Gate

`CLOUD-CONTROL-PLANE-1` remains `DESIGN_VALID` until a real provider/action run proves:

- canonical request -> provider mapping binding;
- no authority widening across translation;
- provider dispatch identity captured;
- timeout-after-dispatch produces `OUTCOME_UNKNOWN`;
- no blind redispatch while ambiguous;
- operation state and authoritative resource state are independently re-observed;
- provider audit evidence is correlated but not treated as sole finality proof;
- provider-native idempotency behavior is tested only where documented for that exact operation;
- retry preserves transition/request/authority binding and changes only `attempt_id`;
- exact implementation SHA, provider/API versions, timestamps, request/operation IDs, and raw observations are preserved.

Every test result ends `VALID`, `INVALID`, or `INDETERMINATE`.
