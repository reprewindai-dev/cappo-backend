# VEKLOM_WID_1_PROOF_REPORT

- **Repository:** cappo-backend
- **Branch:** main
- **Commit SHA:** d2f674478d89dc3d576328ad366542d2824912fe
- **Working tree status:** Clean (all schemas and fixtures committed)
- **Files created:** 
  - schemas/identity/*.schema.json (8 files)
  - tests/fixtures/identity/valid/*.valid.json (8 files)
  - tests/fixtures/identity/invalid/*.invalid.json (10 files)
  - tests/adversarial/test_wid_1_identity_schemas.py
  - docs/identity/VEKLOM_WID_1_SCHEMA_DOCTRINE.md
- **Files modified:** pyproject.toml, uv.lock (added jsonschema dependency)
- **Test command:** uv run pytest tests/adversarial/test_wid_1_identity_schemas.py -v --tb=short
- **Pass/Fail count:** 13 passed, 0 failed
- **Artifact Hashes:**
  - 
eceipt.cose: 8a9abf6ab984c6307392675dc92ccdfa00af93ffc514aa21d65b3f005bbb42bd
  - public-key.pem: dc08c86fb448f53db85daa694c9cca3214057deae6eeaeef117861b819a65b4c
  - proof.json: 6a728982a106ef848e016b0f14cd0bdc8a1c208c2278ce667f15fb616d90a3d6
  - checkpoint.json: 6f6a8ef25d86156205692a082721b0b5f6e7bbabc8a69adef386911f91ac6e69
- **Known limitations:**
  - schemas verified locally only
  - GitHub Actions status checks not required unless present
  - no runtime token middleware yet
  - no CAPPO enforcement yet
  - no P5 truth-transition identity binding yet
  - no PGL identity-chain enforcement yet
  - no runtime service identity or protocol probe yet
- **Next required gate:** WID-2: Token Validation Middleware
