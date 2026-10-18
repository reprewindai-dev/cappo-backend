# Provider Capability Matrix - Forward Scout

**Status:** `RESEARCH / DESIGN INPUT`  
**Rule:** Provider mechanics are operation-specific. Do not infer support for one API from another API in the same cloud.

| Dimension | AWS | GCP | Azure | Veklom Rule |
|---|---|---|---|---|
| Idempotency | Some APIs expose `ClientToken`, `ClientRequestToken`, or service-specific equivalents | Some APIs expose `requestId` or service-specific request identifiers | Semantics vary by ARM/resource-provider operation; correlation headers are not a universal idempotency primitive | Bind only documented operation-specific mechanisms; never invent a universal token |
| Async operation identity | Service-specific request/operation IDs and status APIs | Long-running `Operation` resources or service-specific operation IDs | ARM/resource-provider async operation URLs/IDs where supported | Preserve provider-issued operation identity as evidence |
| Audit trail | CloudTrail / service logs | Cloud Audit Logs | Activity Log / resource diagnostic logs | Corroborating provenance, not sole finality proof |
| Authoritative finality | Service/resource-specific Describe/Get/List APIs and state machines | Resource GET plus operation status | Resource GET plus provider-specific async status | Prefer operation status + authoritative resource readback |
| Retry safety | Depends on exact service/action and documented idempotency semantics | Depends on exact service/action | Depends on resource provider/action | Veklom fences ambiguity regardless of provider retry behavior |
| Provider substitution | Never implicit | Never implicit | Never implicit | New realization requires authorization/binding check |

## Required Adapter Metadata

Every provider implementation should expose a normalized realization record:

```text
provider
account_project_subscription
region_or_scope
service
api_version
operation_name
resource_identity
provider_request_id
provider_operation_id
provider_idempotency_token_or_null
provider_request_digest
mapping_contract_digest
```

## Research Questions Before Each New Provider Action

1. Is the API synchronous or asynchronous?
2. What exact identifier can be used to query operation state?
3. What constitutes terminal provider success vs accepted/pending?
4. What API authoritatively exposes the resulting resource state?
5. Is idempotency documented for this exact action? What are its scope and retention semantics?
6. Can the same token with different parameters fail, replay, or produce a mismatch error?
7. What audit trail exists and what latency/completeness caveats apply?
8. What identifiers link audit events to request/operation/resource?
9. What permissions does the independent observer require?
10. Can the observer mutate the target? It should not.
11. What failure modes produce ambiguous dispatch?
12. What provider behavior occurs if the resource already changed outside Veklom?

## First-Proof Selection Rule

Select an action only if the consequence can be safely created and cleaned up in a non-production account/project/subscription. Avoid destructive or billing-sensitive operations until the generic contract and ambiguity/reconciliation state machine are measured.
