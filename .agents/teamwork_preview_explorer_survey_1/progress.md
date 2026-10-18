# Progress: teamwork_preview_explorer_survey_1

- Last visited: 2026-09-09T14:21:10Z
- Status: In Progress — Synthesizing codebase survey and VRE-0 integration plan
- Current findings:
  1. Codebase layout: `cappo_backend` package, `pyproject.toml` with setuptools/uv, Python >=3.11 target py311.
  2. Execution / Substrate: Existing stub in `cappo_backend/services/substrate.py` (`WasmtimeSubstrate`, `FirecrackerSubstrate`, `SubstrateOrchestrator`). Envelope & Receipt models in `cappo_backend/models/fedcom_v1.py` and `cappo_backend/execution/crypto_envelope.py`.
  3. WSL environment: Ubuntu 26.04 on WSL2, pure cgroup v2 mounted at `/sys/fs/cgroup` with memory/cpu controllers enabled, root/sudo access, direct Windows disk mount at `/mnt/c`. Wasmtime 48.0.0 installed and verified in WSL Python 3.12 environment via `uv`. Cgroup creation, `memory.max`, and process attachment verified.
  4. Testing: pytest 9.1.1 in `.venv313` on Windows host, fixtures in `tests/conftest.py`, hostile workload tests in `tests/adversarial/` and `tests/test_g1_p1_hostile_workload*.py`.
  5. Architecture recommendations formulated for VRE-0 supervisor, Wasmtime host, test wasm/wat modules, receipt models, and verification tests.
