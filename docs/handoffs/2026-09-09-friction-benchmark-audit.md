# Antigravity Handoff — Friction Benchmark Audit

Date: 2026-09-09
Classification: NOTEBOOK BENCHMARK FALSIFIER REVIEW
Authoritative status: **NOTEBOOK 7/7 BASELINE INVALID AS EMPIRICAL EVIDENCE**

## What Notebook supplied

Notebook supplied:
- `benchmark_frictionless_metrics.py`;
- `frictionless_benchmark_baseline.json`;
- a revised wedge dossier claiming 7/7 friction metrics passed;
- a claimed TTFGC baseline of 0.9883 seconds;
- a claimed current outcome of `UNIQUE WEDGE SUPPORTED (LOCAL/STAGING)`.

## Falsifier findings

### FRIC-01 — TTFGC

**INVALID as measured runtime evidence.**

The baseline JSON records a connection failure to `127.0.0.1:8002` while still marking the metric PASS. The Notebook script catches any exception and then sets PASS solely when the elapsed failure time is `< 5.0s`. Therefore the reported `0.9883s` is time-to-failure, not time-to-first-governed-consequence.

The Notebook script also calls SDK methods/signatures that do not match the current provisional SDK (`mint_authority(...)` rather than `mount_authority(...)`, and a different dispatch signature), so it cannot be accepted as a valid runtime benchmark.

### FRIC-02 — Configuration file count

**INVALID as measured evidence.**

If no expected files exist, the Notebook script falls back to `2`, guaranteeing the threshold can pass even when the files are absent.

### FRIC-03 — Manual approval steps

**INDETERMINATE.**

The script hard-codes `manual_approvals = 0`; it does not observe a clean external developer trial.

### FRIC-04 — Installed infrastructure components

**INDETERMINATE.**

The script hard-codes `supervisor_components = 1`; it does not inventory installed components or distinguish Veklom prerequisites from benchmark tooling.

### FRIC-05 — API setup calls

**INDETERMINATE.**

The script hard-codes `api_setup_calls = 1`; it does not trace an actual setup flow.

### FRIC-06 — Recovery steps

**INDETERMINATE.**

The script hard-codes `recovery_steps = 1`; it does not begin with a real `OUTCOME_UNKNOWN` execution and measure reconciliation calls to finality.

### FRIC-07 — Workload secret isolation

**INDETERMINATE.**

The script hard-codes `exposed_secrets = 0`; it does not inspect workload environment/memory/debug capture for known target-secret markers.

### Timestamp / sealing

**INVALID as measured-run metadata.**

The script hard-codes `2026-09-09T14:24:00Z` rather than recording the actual run time and commit SHA.

## Corrected implementation now on branch

A fail-closed replacement has been landed at:

`scripts/benchmark_frictionless_metrics.py`

Commit: `463cac625a4ecfa56f266b3d9686fbdddff909df`

The corrected runner:
- never converts exceptions into PASS;
- returns `INDETERMINATE` when required measurements are absent;
- measures live mount + dispatch for FRIC-01;
- requires an observed trial manifest for FRIC-02 through FRIC-05;
- requires a real `OUTCOME_UNKNOWN` execution for FRIC-06;
- requires a workload capture plus known secret markers for FRIC-07;
- records actual UTC time, git SHA, branch, and evidence details;
- exits `0` only when all seven metrics PASS, `1` on FAIL, and `2` on INDETERMINATE.

## Frozen thresholds

The seven proposed thresholds may remain as **candidate staging thresholds**:

- FRIC-01: TTFGC `< 5.0 seconds`;
- FRIC-02: `<= 2` configuration files touched;
- FRIC-03: `0` manual approval steps during bounded dispatch;
- FRIC-04: `<= 1` additional installed component;
- FRIC-05: `<= 1` setup call before dispatch;
- FRIC-06: `<= 1` automated reconciliation call from known ambiguity to finality;
- FRIC-07: `0` known target-secret markers observed in workload capture.

These thresholds are now frozen for the next real staging trial. They are **not yet passed**.

## Dossier truth correction

Until the corrected benchmark executes against the canonical runtime with measured evidence:

- `frictionless` = **INDETERMINATE**;
- `7/7 METRICS PASSED` = **INVALID**;
- `0.9883s TTFGC` = **INVALID as TTFGC**;
- `UNIQUE WEDGE SUPPORTED (LOCAL/STAGING)` = **INVALID / premature**;
- final Wedge Elimination outcome = **INDETERMINATE**.

The A-L PRODUCT-0 lower-layer proof battery remains CLOSED at its recorded proof surfaces. This audit does not reopen those tests.

## Antigravity work only

Do not reproduce Notebook's benchmark logic. Consume the corrected branch runner and supply the missing real measurements:

1. start canonical CAPPO independently;
2. provide a real benchmark capability fixture;
3. execute a real mount -> dispatch consequence for FRIC-01;
4. record a clean external-developer trial manifest for FRIC-02..05;
5. provision a known `OUTCOME_UNKNOWN` execution and measure reconciliation for FRIC-06;
6. capture workload env/memory/debug surface and scan known target-secret markers for FRIC-07;
7. return raw JSON, commit SHA, branch, command output, and runtime identifiers.

Only after those steps may the friction matrix be upgraded from `INDETERMINATE`.
