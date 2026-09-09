# CAPPO Consequence Contract Corrections

This revision preserves the useful structure from the Gemini Notebook draft while correcting the parts that conflicted with the current Veklom proof doctrine and canonical boundary model.

## Corrections applied

1. **Split OpenAPI and AsyncAPI into separate documents.** The original file declared both `openapi` and `asyncapi` and repeated `info`, which caused YAML loaders to overwrite the first `info` block.
2. **Kept raw Biscuit authority server-side for the browser/session path.** `/mounts` now returns an opaque `authority_reference`, not Biscuit bytes. Dispatch uses a principal/session credential plus opaque lease/mount references; CAPPO resolves server-held authority.
3. **Removed caller-supplied reconciliation URLs.** Reconciliation now accepts `target_binding_id` and `target_resource_id`; CAPPO resolves the observer endpoint from trusted server-side configuration.
4. **Separated transport failure from authority lock semantics.** `/consequence/reconcile` may return HTTP 503 when the observer is unavailable. `/consequence/dispatch` uses HTTP 423 when authority is locked in `OUTCOME_UNKNOWN` or `RECONCILIATION_UNAVAILABLE`.
5. **Calibrated the evidence package.** COSE/correlation fields are explicit. Merkle/SCITT inclusion is optional and only present when E4 transparency evidence actually exists.
6. **Fixed enforcement classification.** Fuel exhaustion is a Wasmtime runtime trap, not Unix exit code 137. `termination_reason` now classifies MEMORY_OOM, COMPUTE_FUEL_EXHAUSTED, DEADLINE_SIGKILL, etc., while raw exit codes remain supporting evidence.
7. **Made replay semantics normative.** Validation precedes the single-use authority claim; consumed authority cannot be resurrected by restart, cache expiry, reconciliation, or retry. Concurrent exact duplicates must yield at most one consequence opportunity.
8. **Made E3 observer independence normative.** Target-side re-observation is resolved from trusted target bindings and remains distinct from executor success narration.

## Files

- `cappo-consequence-dispatch.openapi.yaml` - corrected OpenAPI 3.1 contract.
- `cappo-consequence-events.asyncapi.yaml` - corrected AsyncAPI 3.0 event contract.
