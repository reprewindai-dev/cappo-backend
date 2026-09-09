# Post-Merge Verification Status — PRODUCT-0 / RC1

Date: 2026-09-09
Base: `main`
PR #99 merge commit: `e5b4fade259c83e22ba83d56a5356b9a7eb8f934`

## Purpose

This document is a factual status record for the repository after PR #99. It is not an instruction file for an AI system and does not assert completion where repository evidence is incomplete.

## Verified repository state

- Canonical mount route: `POST /v1/capability/mounts`.
- Server-side mount issuance flows through `MountRegistry.request_mount(...)` and internal Biscuit minting.
- The FastAPI mount response currently contains `token: EphemeralScopedToken | None` and returns `token=record.token` on success.
- `EphemeralScopedToken` still includes `biscuit_token`; therefore browser/session response custody is not yet verified as server-held on `main`.
- Python and TypeScript SDKs on `main` still use `/v1/mounts` and remain provisional.
- `contracts/cappo-consequence-dispatch.openapi.yaml` is not present on `main`.
- `scripts/run_hostile_port8002_battery.py` on `main` is client-only and requires an independently running CAPPO service plus deterministic fixture environment values.
- PRODUCT-0 A-L remains closed at its recorded proof surfaces.

## Remaining verification items

1. **Mount response custody**
   - Public mount responses should omit raw `biscuit_token` material.
   - Internal persisted authority must remain available for server-side verification.
   - Repository tests should verify both properties separately.

2. **SDK/runtime schema alignment**
   - SDK mount calls should target `/v1/capability/mounts`.
   - Request and response models should match the actual FastAPI/OpenAPI schema on the same commit.
   - Typed handling for 401/403/422/423/503 should remain consistent with runtime behavior.

3. **Normative OpenAPI**
   - The final OpenAPI should be generated or maintained against the actual runtime schema after the response-custody boundary is finalized.
   - Reconciliation unavailability remains HTTP 503.
   - Replay or redispatch while already locked remains HTTP 423.
   - Canonical spelling is `RECONCILED_SUCCEEDED`.

4. **Deterministic runtime fixtures**
   - The live hostile battery requires server-side fixture states for valid, consumed, revoked, mismatched, locked, and reconciliation cases.
   - Fixture creation must exercise the trusted server issuance path rather than a client-side authority-mint shortcut.

5. **Live hostile runtime evidence**
   - Remaining coverage includes envelope substitution, reconciliation-unavailable, reconciliation-recovery, and mount-response custody.
   - Accepted evidence should come from an independently started CAPPO/Uvicorn process with real timestamps, response bodies, and commit metadata.

6. **Friction benchmark**
   - `scripts/benchmark_frictionless_metrics.py` is the canonical fail-closed benchmark.
   - The earlier Notebook `0.9883s` / 7-of-7 result is not accepted as a live measurement.

7. **Competitor/Wedge status**
   - Simulated competitor results are not provider reproduction evidence.
   - Final Wedge Dossier status remains `INDETERMINATE` until canonical W1 integration, live friction measurements, and real provider reproductions are evidenced.

## Evidence expected for closure

Closure evidence should include changed file paths, commit SHA, raw test output, a real FastAPI mount response showing no raw Biscuit token, confirmation that persisted internal authority still verifies, and client-only runtime evidence against an independently started CAPPO service.
