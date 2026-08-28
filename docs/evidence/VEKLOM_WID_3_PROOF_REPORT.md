# VEKLOM_WID_3_PROOF_REPORT

- **Repository:** cappo-backend
- **Branch:** main
- **Commit SHA:** e1a319d4461f50300fe238a840c47c9cad9f8d89
- **Working tree status:** Clean (all authorization logic and tests committed)
- **Files created:** 
  - cappo_backend/authorization/cappo_auth.py
  - cappo_backend/authorization/errors.py
  - tests/adversarial/test_wid_3_cappo_profile_only_denial.py
  - docs/evidence/VEKLOM_WID_3_PROOF_REPORT.md
- **Files modified:** docs/evidence/VEKLOM_WID_2_PROOF_REPORT.md (hygiene)
- **Test command:** uv run pytest tests/adversarial/test_wid_3_cappo_profile_only_denial.py -v --tb=short
- **Pass/Fail count:** 25 passed, 0 failed
- **Artifact Hashes:**
  - 
eceipt.cose: 8a9abf6ab984c6307392675dc92ccdfa00af93ffc514aa21d65b3f005bbb42bd
  - public-key.pem: dc08c86fb448f53db85daa694c9cca3214057deae6eeaeef117861b819a65b4c
  - proof.json: 6a728982a106ef848e016b0f14cd0bdc8a1c208c2278ce667f15fb616d90a3d6
  - checkpoint.json: 4bda5668b2793612bda1e743aff644303dda2f9cb9fda38da5d12f9ab6819a4d
- **Known limitations:**
  - verified locally only
  - no distributed replay prevention yet unless DB/Redis-backed replay is implemented
  - no runtime service identity or protocol probe yet
  - no production deployment verification yet
  - no WIMSE conformance claim
  - no PGL identity-chain enforcement yet unless explicitly implemented
  - no P5 truth-transition identity binding yet unless explicitly implemented
- **Next required gate:** WID-4: P5 truth.transition identity binding
