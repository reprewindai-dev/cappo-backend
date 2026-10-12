# Governed model inference (`veklom.inference@v1`)

Capability OS surfaces (Blueprint Studio first) call a model through CAPPO, never
directly. The model's answer is a proposal: CAPPO records it as evidence and applies
nothing.

## Operator setup

1. Name the providers the package may use: `INFERENCE_ALLOWED_PROVIDERS=ollama`
   (comma-separated). Empty means the package is not offered.
2. Give the workspace that provider:

   ```bash
   python scripts/register_provider_credential.py --workspace <workspace-id> \
       --provider ollama --base-url http://host.docker.internal:11434
   ```

   An Ollama endpoint must be on a private or loopback address; any other provider must
   be public HTTPS. A key, when needed, is read from the environment variable named by
   `--api-key-env` and stored encrypted with `VAULT_MASTER_KEY`.
3. Set `EXECUTOR_MODE` to anything but `echo` and `LLM_MODEL` to the model to run
   (for Ollama, a model you have pulled, e.g. `llama3.2`).
4. Set `RUNTIME_KIND` and `RUNTIME_INSTANCE` (see `.env.example`). Without them every
   run is refused with `409 RUNTIME_OWNERSHIP_CONFLICT` / `RUNTIME_IDENTITY_UNAVAILABLE`.

CAPPO's own "managed local Ollama" provider (`LLM_PROVIDER_NAME=ollama` /
`LLM_FALLBACK_PROVIDER_NAME=ollama`) stays fail-closed until its local authorizer
exists; register Ollama for the workspace as above instead.

`POST /v1/exec` is also metered by `services/payment_gate.py`: a workspace's daily run
quota is the `quota_limit` of its `free_run_quota` row (default 1), and a call past it
is refused with HTTP 402 `quota_exhausted`. Raise that row's `quota_limit` for a
workspace that needs more runs.

## Calling it

1. `POST /v1/capability/mounts` with `package_ref: "veklom.inference@v1"` and
   `requested_action_scope.writes: ["llm.exec"]`. The response's `token` carries
   `token_id`, `nonce` and `execution_id`.
2. `POST /v1/exec` with `action: "llm.exec"`, `directive: "ALLOW"`, the prompt and
   `capability_lease: {mount_id, token_id, nonce, execution_id}`, plus the
   `Workload-Identity`, `Execution-Context` and `Workload-Proof` headers. The
   Execution-Context's `ephemeral_execution_id` must equal the lease's
   `execution_id`.
3. When a cAPI gatekeeper key is configured (always in production), the request also
   carries the gatekeeper's `security` envelope and an RFC 9421 signature covering
   `@method`, `@target-uri`, `content-digest` and the three identity headers.

A lease is single-use: a second call with it is refused (`token_replay`).

## Where authority comes from

- Consequence authority is the capability lease CAPPO resolves from its own registry.
  A `Veklom-Authority` header or any authority in the body is ignored.
- The lease's Biscuit is verified against the workload identity CAPPO's middleware
  verified for the request (the identity it was minted for), and against CAPPO's
  stored revocation state for that execution.
- The provider set is bound into the execution identity from the package definition.
  A `scope.allowed_provider_set` sent by the caller is overwritten, and the executor
  refuses any provider outside the bound set.

## End-to-end check

`scripts/e2e_governed_inference.py` drives a running CAPPO over HTTP the way Capability
OS does: discovery, mount, a real model call, then replay, provider widening,
tampering, missing signature, missing identity, execution mismatch, another
workspace, a terminated mount and an anonymous call. It is for a local test
deployment, since it needs the private JWT and gatekeeper keys (see its docstring).
