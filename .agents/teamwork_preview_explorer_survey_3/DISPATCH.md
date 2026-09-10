# DISPATCH: Survey Explorer 3 - VRE-0 Specification & Architecture Deep-Dive

## 2026-09-09T14:08:15Z

You are `teamwork_preview_explorer_survey_3`.
Working directory: C:\Users\antho\.windsurf\cappo-backend\.agents\teamwork_preview_explorer_survey_3
Project root: C:\Users\antho\.windsurf\cappo-backend
Original Request: C:\Users\antho\.windsurf\cappo-backend\.agents\ORIGINAL_REQUEST.md

Task:
Analyze and formulate detailed architectural requirements for the VRE-0 Hostile Resource Boundary proof:
1. Examine `ORIGINAL_REQUEST.md` requirements R1 (Supervisor & Substrate), R2 (Wasmtime Host Wrapper), R3 (Test Modules: ram_bomb, spin_forever, sleep_or_stall, normal_ok), R4 (Verification & Receipt Generation).
2. Define the exact transition state machine: Authorize -> Materialize -> Execute -> Observe -> Classify -> Receipt.
3. Define the data contracts: ExecutionEnvelope (with digest calculation, limits, UUID, workload spec), SubstrateObservation (cgroup stats, trap reason, OS signal, exit code, execution times), TransitionReceipt (envelope digest, UUID, boundary limits, observed outcome).
4. Define the four WASM modules specifications (WAT text and binary representation) and how each triggers its respective boundary condition (memory limit -> cgroup OOM or host memory trap; fuel exhaustion -> wasmtime fuel trap; wall-clock timeout -> supervisor kill; normal -> clean exit).
5. Specify the verification criteria for VRE-0A (Containment), VRE-0B (Binding), VRE-0C (Evidence Continuity).
6. Write your complete findings to `C:\Users\antho\.windsurf\cappo-backend\.agents\teamwork_preview_explorer_survey_3\analysis.md` and handoff.md.
7. Send a message to parent when done.
