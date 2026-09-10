# ADAPTER-SEMANTIC-ISOLATION-SEAL-GATE

**Status:** PROOF INCOMPLETE / FINAL TRUST-BOUNDARY DELTA REQUIRED  
**Branch baseline:** `product_0_semantic_isolation_proof`  
**Baseline commit:** `6be6d179ad6c15d555852799d5295de36216b31f`

## Purpose

The branch establishes a useful canonical-effect model, explicit provider translation phase, and pre-execution semantic check. It does **not yet prove independent semantic isolation** because the adapter currently owns `reverse_normalize()` and can therefore describe its own provider translation back to CAPPO.

The seal requires CAPPO-owned or otherwise server-trusted normalization of the concrete provider call.

## Required trust boundary

Current shape:

```text
CanonicalEffectRequest
  -> adapter.translate(...)
  -> ProviderTranslation
  -> adapter-owned reverse_normalize()
  -> digest compare
  -> execute_translation()
```

Required shape:

```text
CanonicalEffectRequest
  -> server-bound AdapterBinding
  -> adapter.translate(...)
  -> immutable ProviderCall
  -> CAPPO-owned ProviderProfile.normalize_provider_call(ProviderCall)
  -> compare normalized semantics to authorized CanonicalEffectRequest
  -> execute ProviderCall
```

The adapter must not be able to both mutate the provider call and author the semantic interpretation used to approve that same call.

## Required model/profile

Introduce a server-owned provider profile/normalizer registry whose bound identity includes at minimum:

```text
adapter_ref
adapter_version
provider_api_version
mapping_version or mapping_digest
normalizer identity/version
```

`AdapterBinding` must commit to these values plus `canonical_effect_digest`.

Remove proof-path placeholders such as:

```text
mapping_digest = "synthetic-test-mapping"
capability_id fallback = "test-cap"
consequence_class fallback = "default"
semantic_version hardcode = "1.0"
```

For a governed consequence, those values must originate from the actual bound capability/lease/package/provider profile. Missing required binding metadata must fail closed rather than silently substitute a default.

## Hostile battery — final form

All vectors must pass through the real `execute_consequence()` path and assert **zero provider effect on DENY**.

### ASI-1 Provider payload field injection

Adapter returns a provider call containing an unauthorized extra field while attempting to preserve the original semantic claim.

Expected:

```text
decision = DENY
reason = semantic_translation_drift
execute_provider_call_count = 0
target_effect_count = 0
```

### ASI-2 Adapter substitution

Substitute adapter identity/version after authorization without rebind.

Expected: fail closed before provider execution.

### ASI-3 Provider API-version drift

Change the bound provider API version or provider operation interpretation without a new binding.

Expected: fail closed before provider execution.

### ASI-4 Semantic/consequence-class drift

Provider call normalizes to a stronger/different consequence class than authorized.

Expected: fail closed before provider execution.

### ASI-5 Resource/argument widening

Provider call widens exact resource/arguments to wildcard, alternate resource, extra privilege, or broader selector.

Expected: fail closed before provider execution.

### ASI-6 Retry translation drift

Exercise the **full consequence runtime path**, not direct adapter unit calls. Force an ambiguous or retry-eligible control path while preserving the frozen PRODUCT-0 K/L semantics. Attempt to translate the same transition under a changed mapping/provider call.

Expected:

```text
no blind redispatch
no changed provider realization under the same bound transition
target_effect_count <= 1
translation drift cannot reach provider execution
```

Do not redesign the PRODUCT-0 retry state machine to satisfy this test.

### ASI-7 Lying reverse-normalizer falsifier

This is the decisive new falsifier.

Construct a hostile adapter that:

1. receives an authorized canonical request;
2. emits a provider call that performs a different operation/resource/argument set;
3. attempts to report the original canonical request as the meaning of that call.

Under the old adapter-owned `reverse_normalize()` design this can self-certify. Under the sealed design CAPPO's independent normalizer must derive the drift from the concrete provider call and DENY it.

Expected:

```text
decision = DENY
reason = semantic_translation_drift
provider invocation = 0
physical/target effect = 0
```

## Seal conditions

The invariant may be marked `SEALED` only when all are true:

- provider-call semantics are normalized by CAPPO/server-trusted code, not the candidate adapter;
- binding commits to adapter identity, adapter version, provider API version, mapping identity/version and canonical effect digest;
- no synthetic/default binding metadata is used in the governed path;
- ASI-1 through ASI-7 pass through the actual consequence runtime where applicable;
- every deny vector proves zero provider invocation/effect;
- retry vector preserves the existing PRODUCT-0 K/L ambiguity and fencing contract;
- focused regression suite passes;
- exact branch commit and raw test output are retained.

## Stop rule

Once these conditions pass, **STOP PRODUCT-0 work**. Do not invent another semantic-isolation milestone.

The next program is `PUBLIC-SITE-AVAILABILITY-1` in `veklom-FRONTEND`. The website hardening/edge-separation gate is P0 before additional substrate expansion or category-launch activity.
