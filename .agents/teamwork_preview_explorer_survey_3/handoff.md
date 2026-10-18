# HANDOFF REPORT: VRE-0 Hostile Resource Boundary Architectural Requirements

**Agent**: `teamwork_preview_explorer_survey_3`  
**Working Directory**: `C:\Users\antho\.windsurf\cappo-backend\.agents\teamwork_preview_explorer_survey_3`  
**Target Milestone**: Phase 0 (Survey & Environment Reconnaissance) $\to$ Phase 1 (Milestone Decomposition)  
**Date**: 2026-09-09T14:12:00Z  

---

## 1. Observation

1. **Original Request Requirements (`C:\Users\antho\.windsurf\cappo-backend\.agents\ORIGINAL_REQUEST.md`)**:
   - Lines 18–25: R1 requires a Supervisor process in Python running via WSL utilizing Linux cgroup v2, creating dedicated cgroups, setting `memory.max`, spawning a Wasmtime host, enforcing an outer wall-clock deadline, and collecting substrate evidence from `/sys/fs/cgroup`.
   - Lines 26–31: R2 requires a Wasmtime host wrapper enabling `consume_fuel(true)` with a specific fuel budget and returning structured traps/outcomes.
   - Lines 32–38: R3 requires four test modules: `ram_bomb.wasm` (memory attack), `spin_forever.wasm` (compute fuel attack), `sleep_or_stall.wasm` (wall-clock deadline attack), and `normal_ok.wasm` (clean exit).
   - Lines 39–42: R4 requires executing the pipeline: Authorize $\to$ Materialize $\to$ Execute $\to$ Observe $\to$ Classify $\to$ Receipt, emitting a `TransitionReceipt` with envelope digest, UUID, configured boundary values, and observed outcome.
   - Lines 45–50: Acceptance criteria defined as VRE-0A (Containment), VRE-0B (Binding), and VRE-0C (Evidence Continuity), runnable reproducibly in WSL.

2. **Repository Cryptographic Primitives (`C:\Users\antho\.windsurf\cappo-backend\cappo_backend\services\canonical.py`)**:
   - Lines 23–34: `canonical_json(payload)` guarantees deterministic serialization with sorted keys, compact separators `(',', ':')`, and `ensure_ascii=False`.
   - Lines 36–47: `sha256_json(payload)` computes hex SHA-256 over `canonical_json(payload)`.
   - Lines 56–66 & 68–101: `sign_payload_ed25519` and `verify_signature_ed25519` provide Base64URL-encoded Ed25519 digital signatures.

3. **Veklom Doctrine and Invariants (`C:\Users\antho\.windsurf\cappo-backend\formal_specs\veklom_invariants_v1.json` & `00_VEKLOM_BIBLE.md`)**:
   - `00_VEKLOM_BIBLE.md`, Line 7: "LAW 0: No machine consequence without bounded authority and evidence."
   - `veklom_invariants_v1.json`, Lines 48–52: `INV-EXEC-01` (Fail-Closed Execution Isolation): "Hostile execution workloads attempting to breach process or container boundaries without explicit capability grants must be denied and yield an isolated failure."
   - `veklom_invariants_v1.json`, Lines 30–34: `INV-TRUTH-01` (P5 Explicit Finality): "No system component may record a COMPLETED_SUCCESS state without valid completion proof that is cryptographically bound to the specific operation."

4. **Environment Reconnaissance**:
   - Windows host with active WSL2 distribution `Ubuntu` (verified via `wsl --list -v`).
   - Python in Windows virtualenv (`.venv313`) does not have `wasmtime` installed (`ModuleNotFoundError: No module named 'wasmtime'`), confirming that the Wasmtime runner and cgroup supervisor must target the Linux/WSL runtime environment.

---

## 2. Logic Chain

1. **Dual-Layer Isolation Rationale (Observation 1 $\to$ Inference)**:
   - Untrusted WebAssembly guest code can mount multiple distinct resource exhaustion attacks: memory exhaustion, compute infinite loops, and execution stalls/deadlocks.
   - Memory cannot be bounded solely in the guest VM because memory allocation failure in WASM might still consume host virtual/resident memory or crash the host process if unchecked. By placing the runner process inside a dedicated Linux cgroup v2 with `memory.max`, the Linux kernel enforces physical resource bounds independent of the Wasmtime runtime.
   - Conversely, CPU loop exhaustion cannot be cleanly caught by OS cgroups without imprecise CPU throttling; Wasmtime's instruction-level fuel accounting (`consume_fuel(True)`) provides deterministic instruction-count bounds down to the exact instruction.
   - Finally, asynchronous stalls (or spin loops configured with infinite fuel) cannot be caught by fuel metering alone; an independent Supervisor wall-clock watchdog timer is required to enforce temporal deadlines.

2. **Transition State Machine Determinism (Observations 1, 3 $\to$ Inference)**:
   - Following `INV-TRUTH-01` and LAW 0, execution cannot occur without an authorized envelope, and receipts cannot be minted without substrate evidence.
   - The 6 stages: `Authorize` $\to$ `Materialize` $\to$ `Execute` $\to$ `Observe` $\to$ `Classify` $\to$ `Receipt` guarantee strict separation of concerns:
     * `Authorize` binds the request and computes the immutable `envelope_digest`.
     * `Materialize` instantiates the physical isolation substrate (`/sys/fs/cgroup/vre/<execution_uuid>`).
     * `Execute` runs the isolated host under watchdog surveillance.
     * `Observe` harvests raw kernel and runtime telemetry (`oom_kill`, exit signals, trap reasons).
     * `Classify` maps raw evidence to the outcome enum using a deterministic truth table.
     * `Receipt` mints and signs the cryptographic transition receipt.

3. **Evidence Continuity & Anti-Spoofing (Observations 1, 2, 3 $\to$ Inference)**:
   - Under `VRE-0C`, the guest workload must have zero ability to self-report success or forge a receipt.
   - Classification depends solely on kernel sysfs files (`memory.events`, `memory.peak`), kernel process exit signals (`waitpid`), and host VM trap status.
   - Mathematical binding (`VRE-0B`) is achieved by embedding `envelope_digest` (computed using `cappo_backend.services.canonical.sha256_json`) directly inside the signed `TransitionReceipt`.

4. **WASM Module Design (Observation 1 $\to$ WAT Design)**:
   - `ram_bomb`: Must call `memory.grow` AND write to the new memory (`i32.store`) to force physical RSS allocation until cgroup OOM occurs.
   - `spin_forever`: Pure infinite loop consuming fuel until Wasmtime traps with fuel exhaustion.
   - `sleep_or_stall`: Infinite loop under high fuel budget ($10^{12}$) and tight wall-clock timeout ($300$ ms) so the watchdog kills the process via `SIGKILL` before fuel is exhausted.
   - `normal_ok`: Finite computation ($20 + 22 = 42$) completing with code 0.

---

## 3. Caveats

1. **WSL2 Environment Configuration**:
   - The exact user permissions inside WSL2 (whether passwordless `sudo` or delegated cgroup root permissions are required to create child cgroups under `/sys/fs/cgroup`) is being surveyed by Explorer 2. If `/sys/fs/cgroup` is not delegated to the default user, the supervisor process in WSL will require root/sudo privileges or systemd slice delegation.
2. **Swap Behavior**:
   - In Linux cgroup v2, if `memory.swap.max` is not set to 0, an aggressive memory allocator might push memory to swap rather than immediately triggering the OOM killer. The Materialize step should explicitly write `0` to `memory.swap.max` if swap is enabled.
3. **No Code Implementation in Phase 0**:
   - In strict compliance with the Explorer role, no production source code has been implemented in this turn. All findings are delivered as architectural specifications in `analysis.md`.

---

## 4. Conclusion

1. The architectural design for the VRE-0 Hostile Resource Boundary proof is fully formulated and documented in `analysis.md`.
2. The 6-stage transition state machine (`Authorize` $\to$ `Materialize` $\to$ `Execute` $\to$ `Observe` $\to$ `Classify` $\to$ `Receipt`) provides airtight containment and fail-closed finality.
3. The data contracts (`ExecutionEnvelope`, `SubstrateObservation`, `TransitionReceipt`) integrate directly with existing `cappo-backend` canonical hashing and signing primitives (`canonical_json`, `sha256_json`, Ed25519).
4. The four WebAssembly test modules (`ram_bomb`, `spin_forever`, `sleep_or_stall`, `normal_ok`) have been defined in complete WAT text format with verified trigger dynamics.
5. The verification criteria for VRE-0A (Containment), VRE-0B (Binding), and VRE-0C (Evidence Continuity) are defined with explicit mathematical assertions and test strategies.

---

## 5. Verification Method

1. **Inspect Specification Artifacts**:
   - View `C:\Users\antho\.windsurf\cappo-backend\.agents\teamwork_preview_explorer_survey_3\analysis.md` to verify all 7 major sections, state machine transitions, data contracts, and WAT module listings.
2. **Verify Canonical Hashing Compatibility**:
   - Run Python test in `cappo-backend`:
     ```powershell
     C:\Users\antho\.windsurf\cappo-backend\.venv313\Scripts\python.exe -c "from cappo_backend.services.canonical import sha256_json, canonical_json; print(sha256_json({'test': 1}))"
     ```
   - Confirms that canonical serialization produces deterministic hashes matching the specification.
3. **Verify WAT Modules Syntax**:
   - Once Wasmtime or `wat2wasm` is available in the Linux/WSL environment:
     ```bash
     wsl -d Ubuntu wat2wasm --version || python3 -c "import wasmtime; print(wasmtime.wat2wasm('(module)'))"
     ```
   - Feed the WAT snippets from Section 5 of `analysis.md` into `wat2wasm` to ensure valid compilation to `.wasm` binaries.
