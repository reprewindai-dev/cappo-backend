# Activation v1 consequence proof

Evidence label: `VERIFIED_REPO` for the committed harness and sanitized
captures; `VERIFIED_LIVE` for the direct-host run described below.

Source commit under test:

```text
fcff23d762d89e521dca0114508570b3deee47f2
```

Harness version:

```text
activation-v1-harness-2026-09-01.r2
```

The direct-host run used the real FastAPI routes, CAPPO mount registry,
authentication middleware, SPIFFE middleware, Biscuit mint/verify path,
consequence binding, SQLite persistence, and local file-backed effect adapter.
The transport was direct `uvicorn + SQLite`, not Docker:

- `AUTH_ENABLED=true`
- `ENFORCE_SPIFFE=true`
- `ENVIRONMENT=development`
- runtime kind: `uvicorn+SQLite`
- mTLS SPIFFE caller/executor:
  `spiffe://example.org/workload/cappo-backend`
- Biscuit root source: `configured hex` (the value is not recorded)
- effect record root:
  `/home/ubuntu/activation_v1/records`

The local harness exposed the verified mTLS peer certificate in the ASGI TLS
extension expected by the existing SVID middleware. It did not weaken
authentication or CAPPO enforcement.

Docker-host execution remains:

```text
LOCAL-HOST RUNTIME VERIFICATION REQUIRED
```

The Docker compose run was not fabricated because this host lacked the
required `DATABASE_URL` and the pre-existing external networks
`coolify` and `veklom-byos-backend-2_default`.

All HTTP captures in `captures/` are sanitized. Biscuit values, private keys,
JWTs, certificates, nonces, and token IDs are not committed. Presence,
SHA-256, and length are used where Biscuit identity is required. The
committed hashes for every evidence file are listed in `evidence_hashes.json`.
