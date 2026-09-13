# Capability Mount wire specification

The JSON Schemas in `schema/` are the canonical, language-agnostic contract.
Implementations must validate package, mount, token descriptor, and audit event
payloads against those schemas before accepting them.

## Lifecycle

1. **Discover package**: obtain a versioned `CapabilityPackage` such as
   `outreach@v1`. The package declares reads, writes, blocked actions, outputs,
   and policy defaults.
2. **Request mount**: submit the package reference, execution scope
   (`workspace` and `project`), requested action scope, role, policy, and TTL.
3. **Receive mount**: the control plane returns a `Mount` and an
   `EphemeralScopedToken` descriptor. The descriptor contains no bearer secret
   or private-key material.
4. **Present token per action**: the executor binds the descriptor to one
   execution and presents it with each requested action. The binding evaluates
   expiry, termination, blocked precedence, explicit grants, and policy gates.
5. **Receive decision**: an allowed action runs and appends an `allow` event.
   A denied action appends a `deny` event before returning the policy error.
6. **Terminate**: task completion, token expiry, or explicit termination
   unmounts the execution. Subsequent calls are denied.

Tokens are short-lived and single-use by contract. Executors must not persist
memory or authority beyond the mount lifecycle. Audit sinks are append-only and
may be implemented by an in-memory sink for local use or an external ledger
adapter such as PGL.

## Sandbox reference capability: Governed Counter

When the local capability effect root is configured, CAPPO registers the
`veklom.governed-counter@v1` package and its server-owned
`activation.governed-counter` target. The package exposes `counter.read` and
`counter.increment`; `counter.reset` is explicitly blocked. The increment action
advances a per-resource counter by exactly one, regardless of caller arguments.

The target resource is supplied in the execute request and must pass CAPPO's
resource validation. State is stored in the sandbox effect root as
`counter_<resource>.json`, with `value` and `version` fields. A missing state
file starts at zero. `POST /v1/capability/mounts/{id}/execute` resolves the
server-owned target from `target_ref: "activation.governed-counter"`; callers
cannot select an arbitrary implementation. This is the sandbox reference
capability for a bounded, file-backed governed consequence.
