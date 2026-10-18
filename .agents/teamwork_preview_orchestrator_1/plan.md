# Orchestration Plan: VRE-0 Hostile Resource Boundary Proof

## Objective
Implement an executable proof for the VRE-0 Hostile Resource Boundary in `cappo-backend` to demonstrate containment, binding, and evidence continuity using Wasmtime and cgroup v2.

## Phases
1. **Phase 0: Survey & Environment Reconnaissance**
   - Dispatch 3 parallel Explorers:
     - Explorer 1: Inspect `cappo-backend` codebase layout, existing libraries, dependencies, and entrypoints.
     - Explorer 2: Inspect WSL environment, cgroup v2 availability, permissions/delegation, and Python/Wasmtime toolchains.
     - Explorer 3: Detail specification requirements for R1-R4 and VRE-0A/B/C acceptance criteria.
   - Synthesize findings into `PROJECT.md` (Architecture, Feature Inventory, Milestones, Interface Contracts).

2. **Phase 1: Milestone Decomposition & Test Infrastructure Setup**
   - M1: Substrate & Supervisor (cgroup v2 manager, process isolation, wall-clock watchdog, substrate telemetry collection).
   - M2: Wasmtime Host Wrapper & WASM test modules (fuel consumption, memory bounds, traps, normal execution).
   - M3: Verification Pipeline & TransitionReceipt Generation (Authorization, Materialization, Execution, Observation, Classification, Receipt, envelope digest binding).
   - M4: Comprehensive E2E Test Suite & WSL verification runner (VRE-0A, VRE-0B, VRE-0C automated tests).

3. **Phase 2: Iteration Loops (M1 -> M2 -> M3 -> M4)**
   - For each milestone:
     - Explorers analyze milestone details and provide design/fix strategy.
     - Worker implements genuine code, runs builds and local tests.
     - Reviewers evaluate correctness, security, interface conformance.
     - Challengers run stress tests and edge cases.
     - Forensic Auditor verifies integrity (zero tolerance for hardcoding or facades).
     - Gate evaluation.

4. **Phase 3: Final Acceptance & Reporting**
   - Verify 100% pass on WSL with full test suite.
   - Generate final handoff and report to parent.
