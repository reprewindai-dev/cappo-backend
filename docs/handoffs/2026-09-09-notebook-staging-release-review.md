# Notebook Staging / Release Review

Date: 2026-09-09
Classification: NOTEBOOK-GENERATED DEPLOYMENT PLAN - NOT YET STAGING-APPROVED

## Executive verdict

The Notebook staging checklist is useful as a deployment skeleton, but it MUST NOT be used verbatim for staging promotion of `v0.9.0-PRODUCT-0-RC1` yet.

The checklist still incorporates the previously falsified Notebook HTTP harness as if it were a real CAPPO live-runtime test. That harness starts its own Python `HTTPServer` and hard-codes expected responses, so its 10/10 result is simulation evidence, not proof of the canonical CAPPO FastAPI/Uvicorn runtime.

## Keep from the Notebook checklist

Retain these deployment ideas:
- repository-relative contract tooling;
- static OpenAPI/AsyncAPI conformance gate before deployment;
- ingress authority/header custody checks;
- cgroup/fuel/deadline/WAL staging prerequisites where the staging execution profile requires them;
- an independently launched staging CAPPO process;
- a client-only hostile HTTP battery;
- raw response sealing and authority-custody review.

## Required corrections before staging promotion

1. Replace the current `scripts/run_hostile_port8002_battery.py` with a client-only runner. It must never start/listen on port 8002.
2. Launch real CAPPO independently (`uvicorn cappo_backend.api.main:app ...`) and prove readiness before hostile requests.
3. Use canonical server-minted test authority/leases or deterministic trusted test fixtures. Do not use arbitrary `Bearer valid_server_biscuit_token` strings.
4. Record real timestamps and real server headers/bodies.
5. `/v1/consequence/reconcile` observer/target infrastructure unavailability is HTTP 503; a subsequent redispatch while `RECONCILIATION_UNAVAILABLE` remains locked is HTTP 423.
6. Canonical recovery spelling is `RECONCILED_SUCCEEDED`, not `RECONCILIED_SUCCEEDED`.
7. Valid dispatch must conform to the canonical `DispatchResponse` schema (`DISPATCHED` / `COMPLETED` as defined on the landed spec); do not use Notebook's simulated `AUTHORIZED_FOR_MATERIALIZATION` response as normative evidence.
8. HTTP 422 for execution/envelope substitution and HTTP 423 for replay must be present in the exact OpenAPI version deployed to staging.
9. The staging evidence must be generated from the same commit being promoted and must include commit SHA / branch / CI run metadata.
10. Do not create or announce an RC tag until the tag actually exists in GitHub and points at the audited commit.

## Checklist claim corrections

The staging checklist phrase `Confirm 10/10 hostile tests pass` is only acceptable after the client-only real-runtime battery exists.

The release announcement phrase `eliminates post-admission indeterminacy` is too broad. PRODUCT-0 proves bounded authority, ambiguity locking, and reconciliation behavior at the defined proof surfaces. It does not eliminate all indeterminacy for arbitrary external consequences.

Use instead:

> PRODUCT-0 provides fail-closed ambiguity locking, single-use authority, bounded execution, and reconciliation-driven finality for consequences covered by the tested contract.

The statement `Live Runtime Battery ... 10/10 direct HTTP tests passed` is not acceptable until the canonical runtime run is complete. Until then label the Notebook 10/10 as `SIMULATED HTTP CONTRACT HARNESS`.

## Staging promotion gate

STAGING PROMOTION ALLOWED only when:
1. Antigravity local runtime changes are landed on the GitHub branch;
2. static contract gate passes on that commit;
3. the real CAPPO service starts independently in staging/CI;
4. client-only hostile HTTP tests cross the real port-8002 boundary;
5. raw responses validate against the exact OpenAPI on that commit;
6. real WAL/replay/reconciliation behaviors are observed where required;
7. evidence metadata includes actual timestamps + commit SHA + CI run/job identifiers;
8. GitHub tag is created only after the above passes.

## Truth status

- PRODUCT-0 A-L proof battery: CLOSED at recorded proof surfaces.
- Notebook staging checklist: USEFUL DRAFT / REQUIRES CORRECTIONS ABOVE.
- Notebook release announcement: NOT READY FOR PUBLIC/INTERNAL RELEASE AS WRITTEN.
- Notebook 10/10 hostile HTTP result: VALID SIMULATION / NOT canonical live runtime proof.
- Canonical staging promotion: PENDING real client-only runtime evidence.
