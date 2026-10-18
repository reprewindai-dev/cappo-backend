# VEKLOM WID-6 RUNTIME PROOF REPORT

**Subject:** Runtime Identity and Evidence Probes  
**Claim ID:** `VEKLOM-CLAIM-WID-6-001`  
**Status:** REPORTED_RUNTIME  
**Date:** 2026-08-28

## Verifier Context

**Repository:** `cappo-backend`  
**Branch:** `main`  
**Working Tree Status:** Clean  
**Verifier Identity:** Antigravity (Coding Agent)

### Scripts Created
- `scripts/runtime/run_wid6b_probes.py` (Live Probe Execution)

### Probe Artifacts
- `docs/evidence/runtime/wid6_probe_summary.json`
- `docs/evidence/runtime/wid6_negative_probes.jsonl`
- `docs/evidence/runtime/wid6_positive_probe.json`
- `docs/evidence/runtime/wid6_service_identity.json`
- `docs/evidence/runtime/wid6_route_listener_proof.json`
- `docs/evidence/runtime/wid6_redaction_manifest.json`
- `docs/evidence/runtime/wid6_artifact_hashes.json`

## Test Execution Results

**Command:** `uv run python scripts/runtime/run_wid6b_probes.py`  
**Outcome:** Cloudflare Argo Tunnel proved reachable, executing probes against `https://cappo.veklom.com` successfully. The deployed SHA `f6c0dd217a32f05db407959923f8208f0bc56a4d` was proven via the `/runtime/identity` endpoint. All negative probes (missing/malformed WID/ECT/WPT/Authority and PGL missing identity chain) returned 403 Forbidden. The positive valid identity chain was accepted.

## Claim Boundaries

This report supports only the following claim:

> WID-6 live runtime probes were successfully executed against `https://cappo.veklom.com`. The service identity was authenticated and the identity boundaries (WID-2 through WID-5) successfully rejected all negative protocol probes. A valid identity chain correctly authorized consequence paths through the probe harness.

## Limitations

- The probes use dedicated harness endpoints, mock replay behavior, and hardcoded hashes rather than the real production consequence and append routes.
- The claim registry does not bind the runtime result to a deployed artifact digest or exact deployed source SHA. The reported deployed SHA (`f6c0dd2`) was a previous commit rather than the probe commit.
- No WIMSE conformance claim.
- No SCITT conformance claim.

## Next Required Gate

`RTV-1` (Runtime Verification Hardening)
