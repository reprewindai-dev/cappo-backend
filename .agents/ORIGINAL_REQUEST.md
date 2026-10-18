# Original User Request

## 2026-09-09T14:06:42Z

# Teamwork Project Prompt — Final

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Full team

Implement an executable proof for the VRE-0 Hostile Resource Boundary in `cappo-backend` to demonstrate containment, binding, and evidence continuity using Wasmtime and cgroup v2.

Working directory: C:\Users\antho\.windsurf\cappo-backend
Integrity mode: benchmark

## Requirements

### R1. Supervisor & Substrate
Implement a Supervisor process in Python that:
- Runs via WSL (since host is Windows) to utilize Linux cgroup v2.
- Creates a specific cgroup for the execution and sets `memory.max`.
- Spawns a Wasmtime host process inside that cgroup.
- Enforces an outer wall-clock deadline on the execution.
- Collects substrate evidence (memory.current, memory.max, cgroup.procs, exit code/signal) directly from `/sys/fs/cgroup`.

### R2. Wasmtime Host Wrapper
Implement a Wasmtime host in Python that:
- Loads a provided `.wasm` module.
- Enables `consume_fuel(true)` and sets a specific fuel budget.
- Returns a structured completion or trap reason (e.g., fuel exhaustion).

### R3. Test Modules
Provide four simple compiled WebAssembly modules (or WAT files that can be compiled):
1. `ram_bomb.wasm` (allocates memory until failure).
2. `spin_forever.wasm` (infinite loop).
3. `sleep_or_stall.wasm` (blocks/sleeps or spins without consuming much fuel to trigger wall-clock timeout).
4. `normal_ok.wasm` (completes normally).

### R4. Verification & Receipt Generation
For all four scenarios, the Supervisor must run the entire pipeline: Authorize → Materialize → Execute → Observe → Classify → Receipt.
The emitted `TransitionReceipt` must include: envelope digest, execution UUID, configured boundary values, and observed outcome based on substrate evidence (e.g., `ENVELOPE_MEMORY_EXCEEDED`, `ENVELOPE_COMPUTE_EXHAUSTED`, `ENVELOPE_DEADLINE_EXCEEDED`, `COMPLETED`).

## Acceptance Criteria

### Security Enforcement (Tested across all 4 scenarios)
- [ ] **VRE-0A (Containment)**: Host survives. Workload is contained. Receipt outcome strictly matches the violation (OOM vs Fuel vs Timeout vs Normal).
- [ ] **VRE-0B (Binding)**: The logged execution identity, PID, cgroup path, and configured limits mathematically match the signed envelope digest.
- [ ] **VRE-0C (Evidence Continuity)**: The receipt's outcome is reconstructable entirely from substrate-observed evidence (cgroup stats, fuel trap, OS signal), completely independent of the wasm workload's self-report.
- [ ] The full suite runs reproducibly and automatically (e.g., as a pytest suite or a single runner script) in WSL.
