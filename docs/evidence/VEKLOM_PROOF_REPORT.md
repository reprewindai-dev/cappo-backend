# Veklom Proof Artifact: P5 & ZRA-1 Test Execution

**Verifier:** Antigravity (Coding Agent)  
**Timestamp:** 2026-08-28T09:47:35-04:00  

## Source Identity
- **Repository:** `cappo-backend`
- **Branch:** `main`
- **Commit SHA:** `77158bdeb42be7547fd807907b394e196cfcacbc`
- **Working Tree Status:** Clean (0 uncommitted changes)

## Execution Command
```bash
uv run pytest tests/adversarial/ -v --tb=short 2>&1
```

## Results
**Pass/Fail Count:** 87 passed, 1 warning (deprecation), 0 failed
**Execution Time:** ~20.0s

All P5 tests and ZRA-1 hostile tests passed on the clean commit, demonstrating the deterministic `event_sequence` ordering constraint holds and no side-effects occur.

## Output Artifact Hashes (SHA-256)
- `receipt.cose`: `8a9abf6ab984c6307392675dc92ccdfa00af93ffc514aa21d65b3f005bbb42bd`
- `public-key.pem`: `dc08c86fb448f53db85daa694c9cca3214057deae6eeaeef117861b819a65b4c`
- `proof.json`: `6a728982a106ef848e016b0f14cd0bdc8a1c208c2278ce667f15fb616d90a3d6`
- `checkpoint.json`: `334df7a3bf227ebb2ab2bedf2f2a36f879fdf927b3277c807c20ef68ad8a2be4`

## Known Limitations & Claim Status Updates
- **P5 Status Updated to `BOUNDED CI_VERIFIED`**: Hostile tests passed for the attached commit and artifact set; full closure remains gated on runtime integration, CAPPO truth-transition enforcement, PGL binding, and claim-registry reconciliation.
- **ZRA-1 Status Updated to `CI_VERIFIED`**: Validated in clean local CI boundary. Stale handle survivability structurally denied. Awaiting runtime probe against live environment.

The Canonical Claim Registry has been updated accordingly.