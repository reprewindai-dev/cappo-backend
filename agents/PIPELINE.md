# Veklom Pipeline — Architecture Contract

> **DESIGN / SOURCE CONTRACT — NOT RUNTIME EVIDENCE**
>
> This document describes the intended deployment and governance pipeline. It must
> not be read as proof of current production health, traffic, latency, uptime,
> vulnerability state, deployed SHA, or service availability. Operational values
> are `NOT_VERIFIED` unless backed by attributable runtime evidence.

## Responsibility boundaries

- **cAPI** — canonical Interlink / connection contract.
- **CAPPO** — governance, authorization, LAW 0 enforcement, and governed execution decision boundary.
- **Gnomledger** — durable evidence, provenance, and append-only execution history.
- **BYOS** — governed execution substrate / capability supply.
- **Lockerphycer** — governed security, key, and identity surface.

These roles must not be collapsed in examples or dashboard copy.

## Canonical CAPPO runtime contract

| Property | Source contract |
|---|---|
| Application listener | `8002` |
| Container / service target | `8002` |
| Health endpoint | `/health` |
| Deployed SHA | `NOT_VERIFIED` until independently observed |
| Traefik target | `NOT_VERIFIED` until independently observed |
| HTTP / protocol identity | `NOT_VERIFIED` until independently observed |
| Production health | `NOT_VERIFIED` until independently observed |

Production/root CAPPO examples must not normalize ports `3000` or `8000`.

## Intended pipeline

```text
SOURCE -> BUILD -> VALIDATE -> TEST -> STAGE -> GATE -> DEPLOY -> VERIFY
```

### SOURCE

Accept an attributable source revision (commit SHA / reviewed PR). A branch name
or dashboard label by itself is not deployment evidence.

### BUILD

Produce immutable build artifacts from the selected source revision. A successful
local build does not establish production deployment.

### VALIDATE

Blocking validation should include, as applicable:

- Ruff / static checks
- dependency and vulnerability checks
- repository secret scan
- configuration regression checks
- contract/schema checks
- CodeQL or equivalent security analysis

A workflow that fails before executable steps run is **not** a code-verification
result and must not be promoted to PASS.

### TEST

Run focused negative/security tests plus the full applicable suite. Security
remediations require negative-path tests proving the old bypass no longer works.

### STAGE

Any canary or staging state is `NOT_VERIFIED` unless the measurement source,
revision, environment, and timestamp are attached. Do not use invented traffic,
latency, CPU, memory, error-rate, or uptime values as realistic placeholders.

### GATE

A merge/deploy gate must be based on exact-head checks that actually executed.
Non-gating automation (for example release-note drafting) must not be confused
with correctness/security verification.

### DEPLOY

Deployment must inject secrets from the authoritative deployment store. Repository
examples must not contain production credentials, private service topology, or
concrete secret-bearing DSNs.

### VERIFY

Runtime becomes verified only when independent observations agree on the same
revision and service identity. At minimum, record:

```yaml
runtime_verification:
  source_sha: NOT_VERIFIED
  cappo_listener: 8002
  http_identity: NOT_VERIFIED
  health_protocol: NOT_VERIFIED
  traefik_target: NOT_VERIFIED
  capi_to_cappo_auth: NOT_VERIFIED
  gnomledger_evidence_path: NOT_VERIFIED
```

Replace `NOT_VERIFIED` only with measured evidence and its provenance. Never
substitute design intent, a successful merge, or a synthetic dashboard value.

## Dashboard rendering rules

A UI may visualize the pipeline, but unmeasured fields must stay visibly unknown.
Use states such as:

- `NOT_VERIFIED`
- `UNAVAILABLE`
- `PENDING_MEASUREMENT`
- `EXAMPLE_ONLY`

Do **not** ship realistic-looking placeholder values for uptime, request counts,
latency, resource use, connection counts, vulnerability counts, deployment SHAs,
service health, or release state. Do not label a service `Healthy`, `ACTIVE`, or
`VERIFIED` without an attributable measurement.

Example safe design mock:

```text
CAPPO
  listener: 8002
  deployed SHA: NOT_VERIFIED
  health: NOT_VERIFIED
  latency: UNAVAILABLE
  error rate: UNAVAILABLE
  routing: NOT_VERIFIED
```

## Evidence rule

Source truth and runtime truth are separate:

1. **Source-correct** means repository code/config/docs no longer assert a known falsehood.
2. **CI-verified** means exact-head executable checks ran and passed.
3. **Runtime-verified** means deployed SHA, listener, protocol identity, routing, and dependent-service behavior were independently observed.

No lower stage may be promoted into a higher one by wording alone.
