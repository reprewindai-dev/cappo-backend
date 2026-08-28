# VEKLOM WID-5 PROOF REPORT

**Subject:** PGL Identity Chain Enforcement  
**Claim ID:** `VEKLOM-CLAIM-WID-5-001`  
**Status:** BOUNDED LOCAL-CI VERIFIED  
**Date:** 2026-08-28

## Cryptographic Artifact Hashes

- **receipt.cose:** 8a9abf6ab984c6307392675dc92ccdfa00af93ffc514aa21d65b3f005bbb42bd
- **public-key.pem:** dc08c86fb448f53db85daa694c9cca3214057deae6eeaeef117861b819a65b4c
- **proof.json:** 6a728982a106ef848e016b0f14cd0bdc8a1c208c2278ce667f15fb616d90a3d6
- **checkpoint.json:** 3fb3d3b4eac26f0705862737577b2885333893741235174ba0d081e72b0286a5

## Verifier Context

**Repository:** `cappo-backend`  
**Branch:** `main`  
**Source Commit SHA:** `bae5d291d0b78d94ed7b20356d31a2af2e88a373`  
**Working Tree Status:** Clean  
**Verifier Identity:** Antigravity (Coding Agent)

### Files Created
- `cappo_backend/pgl/__init__.py`
- `cappo_backend/pgl/errors.py`
- `cappo_backend/pgl/evidence_validator.py`
- `tests/adversarial/test_wid_5_pgl_identity_chain_enforcement.py`

## Test Execution Results

**Command:** `uv run pytest tests/adversarial/test_wid_5_pgl_identity_chain_enforcement.py -v --tb=short`  
**Outcome:** 30 passed, 1 warning in 0.97s

## Claim Boundaries

This proof supports only the following claim:

> PGL locally refuses to append governed evidence unless it contains a complete, coherent, hash-bound identity chain. Evidence without identity is rejected.

## Limitations

- Verified locally only.
- No runtime service identity or protocol probe yet.
- No production deployment verification yet.
- No external auditor verification yet.
- No WIMSE conformance claim.
- No SCITT conformance claim.
- No runtime PGL service deployment proof yet.

## Next Required Gate

`WID-6: Runtime proof bundle`
