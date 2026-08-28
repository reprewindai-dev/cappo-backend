# VEKLOM WID-6 RUNTIME PROOF REPORT

**Subject:** Runtime Identity and Evidence Probes  
**Claim ID:** `VEKLOM-CLAIM-WID-6-001`  
**Status:** SOURCE_OBSERVED  
**Date:** 2026-08-28

## Verifier Context

**Repository:** `cappo-backend`  
**Branch:** `main`  
**Working Tree Status:** Clean  
**Verifier Identity:** Antigravity (Coding Agent)

### Scripts Created
- `scripts/runtime/wid_6_runtime_probe.sh`
- `scripts/runtime/wid_6_runtime_probe.ps1`

### Probe Artifacts
- `docs/evidence/runtime/wid6_probe_summary.json`
- `docs/evidence/runtime/wid6_negative_probes.jsonl`
- `docs/evidence/runtime/wid6_positive_probe.json`
- `docs/evidence/runtime/wid6_service_identity.json`
- `docs/evidence/runtime/wid6_route_listener_proof.json`
- `docs/evidence/runtime/wid6_redaction_manifest.json`
- `docs/evidence/runtime/wid6_artifact_hashes.json`

## Test Execution Results

**Command:** `powershell -ExecutionPolicy Bypass -File scripts/runtime/wid_6_runtime_probe.ps1`  
**Outcome:** Scripts executed successfully and generated `NOT_VERIFIED` placeholder artifacts.

## Claim Boundaries

This report supports only the following claim:

> WID-6 runtime verification scripts have been authored to probe deployed CAPPO/PGL paths, verify service identity, routing proofs, and execute both positive and negative protocol probes (missing/malformed identity, missing PGL identity chain). The scripts run locally but the runtime probe phase has not been completed.

## Limitations

- Verified scripts created locally only.
- Deployed source SHA cannot be proven yet.
- No deployed service identity or protocol probe captured.
- No production deployment verification yet.
- No WIMSE conformance claim.
- No SCITT conformance claim.

## Next Required Gate

`WID-6: Runtime proof bundle execution` (Execute the probes against a live runtime environment)
