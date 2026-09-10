# VRE-0 Hostile Resource Boundary Proof: Architectural Requirements & Specification Deep-Dive

**Document Version:** 1.0.0  
**Author:** `teamwork_preview_explorer_survey_3`  
**Date:** 2026-09-09T14:11:30Z  
**Target Repository:** `cappo-backend`  
**Standard Compliance:** Veklom Capability OS Core Doctrine (LAW 0, INV-EXEC-01, INV-TRUTH-01, INV-EVID-01)

---

## 1. Executive Summary & Architectural Overview

The **VRE-0 (Veklom Runtime Environment - Level 0) Hostile Resource Boundary Proof** provides empirical, falsifiable verification that untrusted, adversarial guest execution workloads are strictly contained within pre-authorized operational boundaries. Under Veklom Engineering Doctrine (**LAW 0: No machine consequence without bounded authority and evidence** and **INV-EXEC-01: Fail-Closed Execution Isolation**), a host system must never trust guest execution workloads to self-police, self-throttle, or self-report resource consumption or completion status.

The objective of this specification is to define the exact end-to-end architecture, transition state machine, data contracts, WebAssembly modules, and verification criteria for VRE-0. The boundary operates across two complementary enforcement layers:
1. **Host-Level OS Substrate Isolation (Linux cgroup v2)**: Enforces hard physical memory limits (`memory.max`) and process termination via kernel signals (`SIGKILL`, cgroup OOM killer), managed by a sovereign Supervisor process running in Linux (via WSL2 on Windows hosts).
2. **Virtual Machine Compute Isolation (Wasmtime VM)**: Enforces deterministic CPU compute metering via fuel accounting (`consume_fuel(True)`), instruction-level trap interception, and isolated execution memory spaces.
3. **Outer Wall-Clock Watchdog**: An asynchronous watchdog timer in the Supervisor that unconditionally bounds execution wall-clock duration, mitigating asynchronous deadlocks, sleep attacks, or stalling workloads.

Every run transitions through an immutable 6-stage lifecycle:
$$\text{Authorize} \longrightarrow \text{Materialize} \longrightarrow \text{Execute} \longrightarrow \text{Observe} \longrightarrow \text{Classify} \longrightarrow \text{Receipt}$$

The final artifact is a cryptographically signed `TransitionReceipt` that binds the observed substrate facts to the pre-authorized `ExecutionEnvelope`, satisfying **VRE-0A (Containment)**, **VRE-0B (Binding)**, and **VRE-0C (Evidence Continuity)**.

---

## 2. Requirements Examination (R1 – R4)

### 2.1 R1: Supervisor & Substrate
- **Execution Context**: Runs as a sovereign controller in Linux (utilizing WSL2 on the Windows host).
- **Substrate Hierarchy (cgroup v2)**:
  - Base mount: `/sys/fs/cgroup`.
  - Dedicated slice/directory per execution: `/sys/fs/cgroup/vre/<execution_uuid>`.
  - Controller enablement: Write `+memory` to parent `cgroup.subtree_control`.
  - Boundary configuration: Write `envelope.limits.memory_bytes` to `memory.max`.
  - Process placement: Write the spawned Wasmtime runner process PID to `cgroup.procs`.
- **Outer Wall-Clock Watchdog**:
  - Independent monotonic timer armed with `envelope.limits.wall_clock_timeout_ms`.
  - On expiration, supervisor issues `SIGKILL` (via `cgroup.kill` or direct process `kill -9`).
- **Substrate Telemetry Collection**:
  - Collect raw kernel metrics directly from `/sys/fs/cgroup/vre/<execution_uuid>/`:
    * `memory.current`: Current bytes allocated.
    * `memory.peak`: Peak bytes allocated during the lifetime of the cgroup.
    * `memory.events`: Specifically `oom` and `oom_kill` counters.
    * `cgroup.procs`: Ensure process count reaches 0.
  - Collect process exit metrics from `waitpid`: exit code and terminating signal (`signal_number`).
  - Substrate cleanup: Remove cgroup directory (`rmdir`) post-observation, ensuring zero resource leakage.

### 2.2 R2: Wasmtime Host Wrapper
- **VM Engine Configuration**:
  - `wasmtime.Config`:
    * `consume_fuel = True` (activates instruction-level deterministic fuel metering).
    * WASI enabled only if explicitly configured; default is pure WebAssembly without ambient host I/O.
- **Store & Resource Initialization**:
  - `wasmtime.Store`:
    * Fuel budget set via `store.set_fuel(envelope.limits.fuel_budget)`.
- **Trap Interception & Diagnostics**:
  - Executes module entrypoint (`run()`).
  - Catches `wasmtime.Trap` and inspects trap code/message:
    * Fuel Exhaustion: trap contains `"all fuel consumed by WebAssembly"`.
    * Memory Out-of-Bounds: trap contains `"out of bounds memory access"`.
    * Unreachable / Abort: trap contains `"unreachable"`.
- **Telemetry IPC**:
  - Returns structured telemetry payload to Supervisor via standard file descriptor / IPC channel:
    `{ "trap_occurred": bool, "trap_reason": str | null, "fuel_consumed": int, "fuel_remaining": int, "duration_ns": int, "return_value": int | null }`.

### 2.3 R3: Test Modules (Four Adversarial & Baseline Scenarios)
1. `ram_bomb.wasm`:
   - Repeatedly calls `memory.grow` and stores dirty data into newly allocated pages to force physical page allocation by the Linux kernel.
   - Triggers: cgroup v2 `oom_kill` (Kernel terminates host runner via `SIGKILL`) or Wasmtime host memory allocation failure.
2. `spin_forever.wasm`:
   - Infinite compute loop (`br $loop`).
   - Triggers: Wasmtime fuel exhaustion trap when fuel budget reaches zero.
3. `sleep_or_stall.wasm`:
   - A workload that attempts to stall indefinitely without exhausting its fuel budget (e.g., infinite loop configured with massive fuel budget $10^{12}$, or host sleep invocation).
   - Triggers: Supervisor wall-clock watchdog timeout and termination via `SIGKILL`.
4. `normal_ok.wasm`:
   - Benign workload executing a finite calculation (e.g. $20 + 22 = 42$) within memory, fuel, and time limits.
   - Triggers: Clean normal exit (`exit_code == 0`, no traps, no OOM).

### 2.4 R4: Verification & Receipt Generation
- The Supervisor coordinates the complete lifecycle and mints a non-forgeable `TransitionReceipt`.
- The receipt cryptographically binds:
  1. `execution_uuid`
  2. `envelope_digest` (SHA-256 over canonical `ExecutionEnvelope`)
  3. `configured_limits` (as authorized)
  4. `observed_outcome` (derived exclusively from substrate evidence)
  5. `substrate_evidence` (kernel stats, process signals, fuel telemetry)
- Signed with the Supervisor's Ed25519 private key using `cappo_backend.services.canonical`.

---

## 3. The 6-Stage Transition State Machine

```
   ┌──────────────┐
   │ UNINITIALIZED│
   └──────┬───────┘
          │ Authorize (Policy check, build ExecutionEnvelope, compute envelope_digest)
          ▼
   ┌──────────────┐
   │  AUTHORIZED  │
   └──────┬───────┘
          │ Materialize (Generate execution_uuid, create cgroup v2, set memory.max)
          ▼
   ┌──────────────┐
   │ MATERIALIZED │
   └──────┬───────┘
          │ Execute (Spawn runner in cgroup, inject fuel, arm wall-clock watchdog)
          ▼
   ┌──────────────┐
   │  EXECUTING   │
   └──────┬───────┘
          │ Observe (Process terminates or killed; read cgroup stats, exit/signal, traps)
          ▼
   ┌──────────────┐
   │   OBSERVED   │
   └──────┬───────┘
          │ Classify (Deterministic evaluation: Substrate facts -> ObservedOutcome)
          ▼
   ┌──────────────┐
   │  CLASSIFIED  │
   └──────┬───────┘
          │ Receipt (Mint TransitionReceipt, compute receipt_digest, sign with Ed25519)
          ▼
   ┌──────────────┐
   │RECEIPT_MINTED│ (Terminal Finality)
   └──────────────┘
```

### Transition Specifications

#### Transition 1: `Authorize` (`UNINITIALIZED` $\to$ `AUTHORIZED`)
- **Preconditions**: Caller submits workload request (WASM bytecode, entrypoint, requested boundaries, principal identity).
- **Invariants**:
  - Envelope boundaries must be strictly positive and bounded by system ceiling policy (`INV-AUTH-01`).
- **Actions**:
  1. Compute SHA-256 digest of WASM bytecode (`wasm_sha256`).
  2. Construct `ExecutionEnvelope` payload.
  3. Compute `envelope_digest = sha256_json(envelope_payload)`.
  4. Optionally sign envelope with Authorizer key.
- **Postconditions**: Canonical `ExecutionEnvelope` exists in immutable storage.

#### Transition 2: `Materialize` (`AUTHORIZED` $\to$ `MATERIALIZED`)
- **Preconditions**: `ExecutionEnvelope` is valid and signed.
- **Invariants**:
  - Substrate cgroup path must be globally unique per execution run.
  - Subtree controllers must include `memory`.
- **Actions**:
  1. Generate `execution_uuid` (UUIDv4).
  2. Create cgroup directory `/sys/fs/cgroup/vre/<execution_uuid>`.
  3. Write `envelope.limits.memory_bytes` to `/sys/fs/cgroup/vre/<execution_uuid>/memory.max`.
  4. Write `0` (or `max`) to `memory.swap.max` if swap accounting is available to prevent swap bypass.
- **Postconditions**: Substrate cgroup is provisioned and verified in sysfs.

#### Transition 3: `Execute` (`MATERIALIZED` $\to$ `EXECUTING`)
- **Preconditions**: Cgroup v2 directory exists and limits are written.
- **Invariants**:
  - Wasmtime runner process PID must be attached to the target cgroup before guest code starts.
  - Wall-clock watchdog must be armed before or synchronously with process launch.
- **Actions**:
  1. Spawn Wasmtime runner subprocess with parameters: `execution_uuid`, module path, fuel budget.
  2. Attach runner PID to `/sys/fs/cgroup/vre/<execution_uuid>/cgroup.procs`.
  3. Start asynchronous watchdog timer for `envelope.limits.wall_clock_timeout_ms`.
  4. Runner initializes Wasmtime Store with `envelope.limits.fuel_budget` and begins execution of `run()`.
- **Postconditions**: Process is executing under dual cgroup + fuel confinement.

#### Transition 4: `Observe` (`EXECUTING` $\to$ `OBSERVED`)
- **Preconditions**: Process completes, traps, or watchdog timer expires.
- **Invariants**:
  - If watchdog timer expires, Supervisor forcefully issues `SIGKILL`.
  - Telemetry must be captured before cgroup destruction.
- **Actions**:
  1. If watchdog fired: issue `SIGKILL` to cgroup; record `watchdog_triggered = True`.
  2. Wait for process exit (`waitpid`); record `exit_code` and `terminating_signal`.
  3. Read kernel cgroup stats:
     - `memory.peak`: Peak memory in bytes.
     - `memory.events`: Extract `oom` and `oom_kill` counts.
     - `memory.current`: Final memory in bytes.
  4. Read runner output telemetry: `trap_occurred`, `trap_code`, `trap_message`, `fuel_consumed`.
  5. Teardown: Confirm `cgroup.procs` is empty, remove cgroup directory (`rmdir`).
- **Postconditions**: Comprehensive `SubstrateObservation` record constructed.

#### Transition 5: `Classify` (`OBSERVED` $\to$ `CLASSIFIED`)
- **Preconditions**: `SubstrateObservation` is populated with raw kernel and runner facts.
- **Invariants**:
  - Classification is a pure deterministic function: $\mathcal{C}(\text{Envelope}, \text{Observation}) \to \text{ObservedOutcome}$.
  - Guest workload self-report is completely disregarded (**VRE-0C**).
- **Actions**: Evaluate substrate evidence against the classification decision matrix (see Section 4.4).
- **Postconditions**: Canonical `ObservedOutcome` assigned.

#### Transition 6: `Receipt` (`CLASSIFIED` $\to$ `RECEIPT_MINTED`)
- **Preconditions**: Canonical `ObservedOutcome` determined.
- **Invariants**:
  - Receipts are append-only, immutable records (**INV-TRUTH-01**).
  - Every receipt must be verifiable independently via public key cryptography.
- **Actions**:
  1. Assemble `TransitionReceipt` containing envelope digest, execution UUID, configured limits, observed outcome, substrate metrics, timestamp, and supervisor identity.
  2. Compute canonical digest: `receipt_digest = sha256_json(receipt_body)`.
  3. Sign `receipt_digest` using Supervisor Ed25519 private key.
- **Postconditions**: Signed `TransitionReceipt` emitted to evidence ledger.

---

## 4. Formal Data Contracts & Cryptographic Specifications

All serialization follows `cappo_backend.services.canonical.canonical_json` (sorted keys, no insignificant whitespace, UTF-8 encoded). All hashes are SHA-256.

### 4.1 Contract 1: `ExecutionEnvelope`

```json
{
  "$schema": "https://veklom.com/schemas/vre/envelope.v1.json",
  "envelope_version": "vre.envelope.v1",
  "envelope_id": "env-7b6c3e21-8f54-4c6e-9311-5d9c2a4128ef",
  "created_at": "2026-09-09T14:15:00.000000Z",
  "authority": {
    "principal": "spiffe://veklom.local/agent/vre-client-01",
    "policy_id": "pol-vre-strict-isolation-v1",
    "lease_id": "lease-98fbc120-d44a-4e2a-9e12-81729cfa44bb"
  },
  "workload": {
    "module_name": "ram_bomb",
    "wasm_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "entrypoint": "run",
    "args": []
  },
  "limits": {
    "memory_bytes": 52428800,
    "fuel_budget": 100000000,
    "wall_clock_timeout_ms": 1000,
    "cpu_shares": 1024
  }
}
```

#### Envelope Digest Calculation
$$\text{envelope\_digest} = \text{SHA-256}(\text{canonical\_json}(\text{envelope\_payload}))$$
This 64-character hexadecimal string binds the identity, workload binary hash, and boundary limits.

---

### 4.2 Contract 2: `SubstrateObservation`

```json
{
  "$schema": "https://veklom.com/schemas/vre/observation.v1.json",
  "observation_version": "vre.observation.v1",
  "execution_uuid": "f2a8910b-419b-4632-a63e-79de60882e31",
  "observed_at": "2026-09-09T14:15:01.120450Z",
  "process_telemetry": {
    "pid": 34192,
    "exit_code": null,
    "terminating_signal": 9,
    "watchdog_triggered": false,
    "wall_clock_duration_ms": 112.45
  },
  "cgroup_telemetry": {
    "cgroup_path": "/sys/fs/cgroup/vre/f2a8910b-419b-4632-a63e-79de60882e31",
    "memory_current_bytes": 0,
    "memory_peak_bytes": 52428800,
    "memory_max_bytes": 52428800,
    "oom_count": 1,
    "oom_kill_count": 1
  },
  "wasm_telemetry": {
    "trap_occurred": false,
    "trap_code": null,
    "trap_message": null,
    "fuel_consumed": 4210000,
    "fuel_remaining": 95790000,
    "return_value": null
  }
}
```

---

### 4.3 Contract 3: `TransitionReceipt`

```json
{
  "$schema": "https://veklom.com/schemas/vre/receipt.v1.json",
  "receipt_version": "vre.receipt.v1",
  "receipt_id": "rcpt-55c328ef-1704-4b5c-897e-128f731bb990",
  "execution_uuid": "f2a8910b-419b-4632-a63e-79de60882e31",
  "envelope_digest": "4a5c898c19a9f939e0d1645e7f96b27e8a349bc96582531e285d89f7f02d41ba",
  "configured_limits": {
    "memory_bytes": 52428800,
    "fuel_budget": 100000000,
    "wall_clock_timeout_ms": 1000
  },
  "observed_outcome": "ENVELOPE_MEMORY_EXCEEDED",
  "substrate_evidence": {
    "peak_memory_bytes": 52428800,
    "oom_kill_count": 1,
    "fuel_consumed": 4210000,
    "duration_ms": 112.45,
    "exit_code": null,
    "terminating_signal": 9,
    "watchdog_triggered": false,
    "host_trap_reason": null
  },
  "issued_at": "2026-09-09T14:15:01.125000Z",
  "supervisor_identity": "spiffe://veklom.local/supervisor/wsl-cgroup2-0",
  "receipt_digest": "62fe1e48325a7a01a91e489c629235e16723ef574c86576b509ef533eb0c7b04",
  "signature": "MEQCIF6b7Z8R2m6q1Q5G3j8S...[Base64URL Ed25519 Signature]..."
}
```

#### Receipt Digest Calculation
$$\text{receipt\_digest} = \text{SHA-256}(\text{canonical\_json}(\text{receipt\_payload\_excluding\_digest\_and\_sig}))$$

---

### 4.4 Outcome Classification Decision Matrix

The pure function $\text{classify\_outcome}(\text{envelope}, \text{observation}) \to \text{ObservedOutcome}$ evaluates the evidence strictly in the following precedence order:

| Precedence | Substrate & Host Condition | Classified Outcome | Rationale |
|---|---|---|---|
| **1** | `oom_kill_count > 0` OR (`terminating_signal == 9` AND `memory_peak >= memory_max`) OR `trap_code == "MEMORY_ALLOC_FAILED"` | `ENVELOPE_MEMORY_EXCEEDED` | OS kernel OOM killer or host allocator intervened due to memory boundary breach. |
| **2** | `trap_occurred == True` AND (`trap_code == "FUEL_EXHAUSTED"` OR `"all fuel consumed" in trap_message`) | `ENVELOPE_COMPUTE_EXHAUSTED` | Wasmtime VM trapped on instruction fuel exhaustion before any OS limit. |
| **3** | `watchdog_triggered == True` OR (`terminating_signal == 9` AND `duration_ms >= wall_clock_timeout_ms`) | `ENVELOPE_DEADLINE_EXCEEDED` | Supervisor hardware/software watchdog killed process after wall-clock expiration. |
| **4** | `exit_code == 0` AND `trap_occurred == False` AND `oom_kill_count == 0` AND `watchdog_triggered == False` | `COMPLETED` | Workload executed completely within authorized bounds. |
| **5** | All other states (e.g. unexpected trap, panic, non-zero exit code without limit breach) | `EXECUTION_FAILED` | Fail-closed outcome for unclassified failures. |

---

## 5. WebAssembly Test Modules Specifications (WAT & Binary)

### 5.1 Module 1: `ram_bomb.wat`
- **Objective**: Breach physical memory boundary by forcing virtual and physical memory allocation.
- **Mechanism**: Calls `memory.grow` repeatedly in a loop, then immediately writes a 32-bit integer into every page to touch the page table and force physical memory commitment by the OS kernel.
- **WAT Source**:
```wat
(module
  ;; Initial memory of 1 page (64KiB)
  (memory (export "memory") 1)
  
  (func (export "run") (result i32)
    (local $pages i32)
    (local $byte_offset i32)
    
    (loop $alloc_loop
      ;; Attempt to grow memory by 16 pages (1 MiB)
      (i32.const 16)
      (memory.grow)
      (local.set $pages)
      
      ;; Check if WebAssembly engine failed to grow (-1)
      (i32.eq (local.get $pages) (i32.const -1))
      (if
        (then
          ;; If grow failed inside VM, trap immediately
          (unreachable)
        )
      )
      
      ;; Calculate byte offset of the newly allocated page block
      (local.set $byte_offset (i32.mul (local.get $pages) (i32.const 65536)))
      
      ;; Write to the memory offset to force physical RSS allocation
      (i32.store (local.get $byte_offset) (i32.const 305419896)) ;; 0x12345678
      
      ;; Continue infinite allocation loop until cgroup OOM kills the process
      (br $alloc_loop)
    )
    (i32.const 0)
  )
)
```
- **Boundary Trigger**:
  - Cgroup `memory.max` is set to 50 MiB (`52428800` bytes).
  - As `ram_bomb` grows memory past ~45–50 MiB, Linux cgroup v2 triggers the kernel OOM killer.
  - Linux kernel sends `SIGKILL` (signal 9) to the host runner process and increments `memory.events:oom_kill`.
  - Supervisor observation detects `oom_kill == 1` and `terminating_signal == 9`.

---

### 5.2 Module 2: `spin_forever.wat`
- **Objective**: Breach CPU compute budget via infinite iteration.
- **Mechanism**: Enters a tight loop incrementing a 64-bit integer.
- **WAT Source**:
```wat
(module
  (func (export "run") (result i32)
    (local $counter i64)
    (local.set $counter (i64.const 0))
    
    (loop $spin
      ;; Increment counter
      (local.set $counter (i64.add (local.get $counter) (i64.const 1)))
      ;; Loop indefinitely
      (br $spin)
    )
    (i32.const 0)
  )
)
```
- **Boundary Trigger**:
  - Supervisor configures Wasmtime Store fuel budget to `10_000_000` instructions.
  - Each loop iteration consumes fuel instructions.
  - After exactly 10,000,000 instructions (~5ms of CPU time), Wasmtime triggers a `Trap` with message `"all fuel consumed by WebAssembly"`.
  - Host catches the trap and records `trap_code = "FUEL_EXHAUSTED"`. Process terminates cleanly with structured trap evidence.

---

### 5.3 Module 3: `sleep_or_stall.wat`
- **Objective**: Breach wall-clock deadline without triggering fuel exhaustion.
- **Mechanism**:
  - Designed as pure WebAssembly (zero external host imports).
  - Executes a compute loop, but is executed under an envelope with an astronomical fuel budget ($10^{12}$ fuel units) paired with a tight wall-clock deadline (e.g., $300$ ms).
  - In 300 ms, a modern CPU executes approximately $5 \times 10^7$ instructions, consuming only $0.005\%$ of the fuel budget.
  - Alternatively, if a host sleep import is configured: imports `env.host_sleep(i32)`.
- **Pure WAT Source (Canonical)**:
```wat
(module
  (func (export "run") (result i32)
    (local $acc i64)
    (loop $stall_loop
      (local.set $acc (i64.add (local.get $acc) (i64.const 1)))
      (br $stall_loop)
    )
    (i32.const 0)
  )
)
```
- **Boundary Trigger**:
  - `envelope.limits.wall_clock_timeout_ms = 300`
  - `envelope.limits.fuel_budget = 1_000_000_000_000`
  - At $t = 300\text{ ms}$, the Supervisor's asynchronous watchdog timer fires.
  - Supervisor terminates the runner with `SIGKILL` (signal 9) and logs `watchdog_triggered = True`.
  - Fuel remaining is $> 99.9\%$; duration is $\ge 300\text{ ms}$.
  - Outcome classified as `ENVELOPE_DEADLINE_EXCEEDED`.

---

### 5.4 Module 4: `normal_ok.wat`
- **Objective**: Execute benign workload to completion within all authorized boundaries.
- **Mechanism**: Computes the sum $20 + 22 = 42$ and returns the integer.
- **WAT Source**:
```wat
(module
  (func (export "run") (result i32)
    (local $a i32)
    (local $b i32)
    (local.set $a (i32.const 20))
    (local.set $b (i32.const 22))
    (i32.add (local.get $a) (local.get $b))
  )
)
```
- **Boundary Trigger**:
  - Consumes $< 100$ fuel units, $< 64$ KiB memory, completes in $< 1$ ms.
  - Returns integer `42`.
  - Process exits with code 0.
  - Outcome classified as `COMPLETED`.

---

## 6. Verification Criteria & Mathematical Proof Formalism

### 6.1 VRE-0A: Containment
**Claim:** The host operating system and supervisor process survive hostile execution workloads without corruption or resource exhaustion; the workload is strictly contained; and the receipt outcome strictly matches the enforced boundary.

#### Mathematical / Logical Assertions:
1. **Host Liveness Invariant**:
   $$\text{ProcessStatus}(\text{Supervisor}) = \text{RUNNING} \quad \forall t \in [T_{\text{start}}, T_{\text{end}}]$$
2. **Strict Outcome Discrimination**:
   $$\mathcal{C}(\text{ram\_bomb}) = \text{ENVELOPE\_MEMORY\_EXCEEDED}$$
   $$\mathcal{C}(\text{spin\_forever}) = \text{ENVELOPE\_COMPUTE\_EXHAUSTED}$$
   $$\mathcal{C}(\text{sleep\_or\_stall}) = \text{ENVELOPE\_DEADLINE\_EXCEEDED}$$
   $$\mathcal{C}(\text{normal\_ok}) = \text{COMPLETED} \land \text{ReturnValue} = 42$$
3. **Substrate Drain Invariant**:
   $$\text{CountProcesses}(\text{cgroup.procs}) = 0 \quad \text{at } T_{\text{post-execution}}$$

---

### 6.2 VRE-0B: Binding
**Claim:** The logged execution identity, host PID, cgroup path, and configured limits mathematically match the signed envelope digest, proving that the execution occurred under the specific authorization.

#### Mathematical / Cryptographic Assertions:
1. **Envelope Digest Match**:
   $$\text{receipt}.\text{envelope\_digest} = \text{SHA-256}(\text{canonical\_json}(\text{ExecutionEnvelope}))$$
2. **Configured Limits Reflection**:
   $$\text{receipt}.\text{configured\_limits} = \text{ExecutionEnvelope}.\text{limits}$$
3. **Substrate Parameter Verification**:
   $$\text{Observation}.\text{cgroup}.\text{memory\_max\_bytes} = \text{ExecutionEnvelope}.\text{limits}.\text{memory\_bytes}$$
   $$\text{Observation}.\text{wasm}.\text{fuel\_budget} = \text{ExecutionEnvelope}.\text{limits}.\text{fuel\_budget}$$
4. **Receipt Signature Authenticity**:
   $$\text{Verify}_{\text{Ed25519}}(\text{receipt}.\text{receipt\_digest}, \text{receipt}.\text{signature}, \text{SupervisorPublicKey}) = \text{TRUE}$$

---

### 6.3 VRE-0C: Evidence Continuity
**Claim:** The receipt's outcome is reconstructable entirely from substrate-observed evidence (cgroup stats, fuel trap, OS signal), completely independent of the WebAssembly workload's self-report.

#### Adversarial Falsification Proof:
1. **Zero-Trust Guest Isolation**:
   - The WebAssembly guest environment is instantiated without file system access, network sockets, or ambient IPC.
   - The guest cannot write to `/sys/fs/cgroup`, cannot alter `waitpid` exit signals, and cannot emit or sign a `TransitionReceipt`.
2. **Independent Reconstructability**:
   - Given an unverified receipt $R$, an independent verifier can recompute the outcome directly:
     $$\mathcal{C}(\text{reconstructed\_observation}) = R.\text{observed\_outcome}$$
   - Even if a malicious guest module exports a function returning `{"status": "COMPLETED", "code": 0}`, the Supervisor derives the outcome exclusively from:
     * Kernel `oom_kill` counter
     * Kernel terminating signal
     * Wasmtime Store fuel trap state
     * Supervisor watchdog timer state

---

## 7. Implementation Blueprint for Subsequent Milestones

To guide Workers in Phase 1 and Phase 2, the following module architecture is specified:

### 7.1 Proposed File Layout
```
cappo_backend/
├── vre/
│   ├── __init__.py
│   ├── contracts.py       # Pydantic / Dataclass models for Envelope, Observation, Receipt
│   ├── supervisor.py      # Master supervisor orchestrator (Authorize -> Materialize -> ... -> Receipt)
│   ├── substrate.py       # Linux cgroup v2 manager (/sys/fs/cgroup filesystem manipulation)
│   ├── wasm_runner.py     # Standalone runner invoking Wasmtime with fuel & memory configs
│   ├── classifier.py      # Pure deterministic classifier mapping substrate facts to outcomes
│   ├── modules/
│   │   ├── ram_bomb.wat
│   │   ├── spin_forever.wat
│   │   ├── sleep_or_stall.wat
│   │   └── normal_ok.wat
tests/
└── vre/
    ├── __init__.py
    ├── conftest.py
    ├── test_contracts.py  # Unit tests for serialization, hashing, and signatures
    ├── test_wat_modules.py# Verification that WAT modules compile and trigger traps
    ├── test_classifier.py # Truth table tests for the deterministic classifier
    └── test_vre0_e2e.py   # Full WSL end-to-end integration test across all 4 scenarios
```

### 7.2 Key Architectural Interfaces

#### `cappo_backend/vre/contracts.py`
- `ExecutionLimits`: `memory_bytes: int`, `fuel_budget: int`, `wall_clock_timeout_ms: int`, `cpu_shares: int = 1024`
- `ExecutionEnvelope`: Canonical model with `.digest() -> str` method.
- `SubstrateObservation`: Observation telemetry model with kernel, process, and VM fields.
- `ObservedOutcome`: `Enum("ENVELOPE_MEMORY_EXCEEDED", "ENVELOPE_COMPUTE_EXHAUSTED", "ENVELOPE_DEADLINE_EXCEEDED", "COMPLETED", "EXECUTION_FAILED")`
- `TransitionReceipt`: Canonical signed receipt model with `.digest() -> str` and `.sign(...)`.

#### `cappo_backend/vre/substrate.py`
- `CgroupSubstrate`:
  - `create_cgroup(execution_uuid: str, memory_max_bytes: int) -> Path`
  - `attach_pid(cgroup_path: Path, pid: int) -> None`
  - `sample_telemetry(cgroup_path: Path) -> CgroupTelemetry`
  - `destroy_cgroup(cgroup_path: Path) -> None`

#### `cappo_backend/vre/supervisor.py`
- `VreSupervisor`:
  - `run_pipeline(envelope: ExecutionEnvelope) -> TransitionReceipt`:
    Executes the 6-stage transition state machine end-to-end.

---

## 8. Summary of Findings

1. The requirements R1-R4 form a coherent, fail-closed containment architecture where OS-level cgroup v2 and VM-level Wasmtime fuel accounting operate in strict defense-in-depth.
2. The 6-stage state machine (`Authorize` $\to$ `Materialize` $\to$ `Execute` $\to$ `Observe` $\to$ `Classify` $\to$ `Receipt`) guarantees that no execution can occur without prior cryptographic authorization, and no receipt can be emitted without independent substrate observation.
3. The data contracts provide tamper-evident mathematical binding between authorization and execution telemetry using existing `cappo-backend` canonical hashing primitives.
4. The 4 WebAssembly modules provide clean, unambiguous trigger mechanisms for each boundary condition.
5. The acceptance criteria (VRE-0A, VRE-0B, VRE-0C) establish an unforgeable standard of proof adhering to Veklom Core Doctrine.
