# Windows VRE Native Binding Specification

Status: PROPOSED / PROOF-GATED
Date: 2026-09-09
Scope: Windows 11 Pro / Hyper-V-capable Windows substrate

## 1. Purpose

Define how Veklom can bind a governed execution to Windows-native compute, identity, network, resource, lifecycle, and telemetry objects without treating those native objects as replacements for CAPPO authority or Veklom evidence semantics.

This specification is intentionally conservative: Windows native objects are execution-substrate references. They are not, by themselves, a CapabilityLease, a cryptographic attestation, or target-side consequence finality.

## 2. Architectural rule

CAPPO remains the authority source. A persisted `CapabilityLease` remains the Veklom authority object. Windows objects materialize and evidence an execution of that authority.

The relationship is:

`CapabilityLease -> WindowsExecutionBinding -> Windows native objects`

NOT:

`CapabilityLease == VM SID`

and NOT:

`WFP policy == ConsequenceAuthority`.

The current CAPPO lease model already carries the durable authority identity and bounds needed for this binding, including `lease_id`, `mount_id`, `capability_id`, `execution_identity`, subject/executor identities, Biscuit hash, lease state, authority/revocation epochs, delegation limits, contextual bounds, offline bounds, and reconciliation state.

## 3. Native Windows binding surface

A Windows execution binding MAY reference the following objects when they exist for the selected isolation mode:

- HCS compute-system identifier / GUID
- VM or container security identifier (SID)
- worker process identifier plus process start time
- access-token identity metadata: user SID, logon SID, integrity level, authentication LUID where observable
- Job Object identity and effective limits
- HCN namespace identifier
- HNS/HCN endpoint identifier
- virtual switch port identifier
- network compartment identifier
- WFP/BFE filter or policy identifiers
- ETW provider GUIDs, Event IDs, record IDs, ActivityIds, RelatedActivityIds, timestamps, and event-loss counters
- VBS/HVCI state as posture evidence
- TPM quote / measured-state evidence only when a real verifiable quote chain exists

A VM SID is a Windows security identifier associated with a virtual machine identity. It MUST NOT be described as a cryptographic signature or hardware attestation.

## 4. Data model

### 4.1 WindowsExecutionBinding

```text
WindowsExecutionBinding {
  binding_id
  lease_id
  execution_id
  workspace_id
  authority_digest
  envelope_digest

  isolation_mode
  compute_system_id?
  vm_sid?
  worker_pid?
  worker_process_start_time?

  token_user_sid?
  token_logon_sid?
  token_integrity_level?
  token_authentication_luid?

  job_object_ref?
  network_namespace_id?
  network_id?
  endpoint_id?
  vswitch_port_id?
  compartment_id?
  wfp_filter_ids[]

  created_at
  observed_at
  binding_digest
}
```

`binding_digest` is computed over canonical serialized binding fields plus `lease_id`, `execution_id`, `authority_digest`, and `envelope_digest`. It detects binding drift; it does not by itself make the observations independently trustworthy.

### 4.2 WindowsVreEnvelope

```text
WindowsVreEnvelope {
  memory_limit_bytes?
  cpu_rate_limit?
  active_process_limit?
  wall_deadline_ms?
  job_time_limit_ms?
  allowed_network_destinations[]
  denied_network_destinations[]
  filesystem_or_device_constraints[]
  isolation_mode
}
```

The adapter MUST read back effective substrate limits after materialization. Declared limits alone are not proof of enforcement.

### 4.3 WindowsNativeEvidence

```text
WindowsNativeEvidence {
  execution_id
  binding_id
  source
  provider_guid?
  event_id?
  event_record_id?
  activity_id?
  related_activity_id?
  native_object_ids[]
  observed_at
  raw_event_digest
  capture_session_id?
  events_lost?
}
```

ETW is a correlation and observation source. ETW records MUST NOT automatically be promoted to E1/E2/E3 merely because they contain ActivityIds. Evidence tier assignment remains governed by Veklom evidence criteria and independent verification requirements.

### 4.4 WindowsTerminationEvidence

```text
WindowsTerminationEvidence {
  execution_id
  binding_id
  compute_system_exit_status?
  worker_exit_code?
  worker_reaped?
  compute_system_absent_after?
  endpoint_absent_after?
  port_absent_after?
  job_empty_after?
  terminated_at
  reason
}
```

HCS termination, process exit, and network teardown prove execution-substrate disposition. They do NOT prove the external consequence succeeded or failed.

## 5. Internal adapter surface

Windows-native details SHOULD remain behind a trusted substrate adapter rather than becoming caller-controlled fields on the public consequence API.

```text
WindowsVreAdapter.prepare(lease, envelope) -> PreparedWindowsExecution
WindowsVreAdapter.instantiate(prepared) -> WindowsExecutionBinding
WindowsVreAdapter.start(binding, workload) -> RuntimeHandle
WindowsVreAdapter.observe(binding) -> WindowsNativeEvidence[]
WindowsVreAdapter.read_effective_bounds(binding) -> EffectiveWindowsBounds
WindowsVreAdapter.terminate(binding, reason) -> WindowsTerminationEvidence
WindowsVreAdapter.verify_binding(binding) -> VALID | INVALID | INDETERMINATE
WindowsVreAdapter.reconcile(binding, target_binding) -> ReconciliationResult
```

Native object identifiers returned by the adapter are observations/results. An untrusted client MUST NOT choose a compute-system ID, VM SID, endpoint ID, vSwitch port ID, Job Object identity, or WFP filter ID and thereby create authority.

## 6. Lifecycle mapping

Veklom consequence state and Windows substrate state are related but distinct.

```text
CAPPO AUTHORIZE
  -> Windows PREPARED
  -> Windows INSTANTIATED
  -> Windows RUNNING
  -> consequence dispatch
  -> target observation
  -> Windows TERMINATING / TERMINATED
  -> evidence finalization
```

`OUTCOME_UNKNOWN` belongs to consequence finality, not Windows process state. A process can be terminated while the external consequence remains unknown.

Likewise:

- HCS termination != consequence success
- process exit code 0 != target-side finality
- VmSwitch/endpoint deletion != business/network consequence finality
- orchestrator success != independent observation

## 7. Authority projection

Windows enforcement is a projection of already-issued Veklom authority.

A Windows execution is admissible only when the effective Windows projection does not widen the lease/package/Biscuit ceiling.

Conceptually:

`EffectiveAuthority(t) = Veklom authority intersection`

and the Windows adapter materializes a subset:

`WindowsProjection(t) <= EffectiveAuthority(t)`

Examples:

- Job Object limits MUST be equal to or narrower than the approved VRE envelope.
- WFP/Firewall egress MUST be equal to or narrower than approved network authority.
- process/token identity MUST match the execution binding expected by the lease.
- lifetime MUST not exceed lease/deadline bounds.

## 8. Evidence and trust boundaries

### 8.1 ETW

ETW is useful for native correlation, but event delivery can be lossy. The collector MUST record session loss counters and treat non-zero or unavailable loss accounting according to the proof policy. A missing event cannot be silently interpreted as proof that an event did not occur.

### 8.2 VM SID / compute-system ID

Where Windows exposes a deterministic relationship between the VM/container identity and compute-system identifier, the verifier SHOULD independently reconstruct or validate that relationship and record the derivation method/version.

This proves native identity binding. It does not prove hardware provenance.

### 8.3 TPM / VBS

VBS/HVCI configuration or `Get-Tpm` state is posture evidence only. Hardware-rooted provenance requires a verifiable attestation/quote whose nonce, PCR selection, signature chain, and measured-state interpretation are validated independently.

### 8.4 PGL

PGL MAY ingest normalized Windows evidence plus digests of retained raw native records. PGL does not convert untrusted telemetry into independent truth merely by storing it. The evidence record MUST preserve source, capture method, observer identity, timestamp, loss state, and integrity mechanism.

## 9. Proof program

This is a new Windows-substrate validation program. It does not reopen or renumber the closed PRODUCT-0 A-L battery.

### WNB-1 Native identity binding

Prove that the observed VM/container SID and compute-system identifier refer to the same native workload using two independent Windows surfaces.

### WNB-2 Compute-to-network binding

Prove that compute-system identity joins to namespace/endpoint/vSwitch-port identity using native object state or event payloads, not timestamp proximity alone.

### WNB-3 Process/token binding

Prove that the worker PID plus process-start identity and access-token metadata bind to the same execution and cannot be confused by PID reuse.

### WNB-4 Resource enforcement

Materialize declared Job Object/HCS limits, read them back, exceed memory/CPU/deadline bounds under hostile load, and capture independent enforcement evidence.

### WNB-5 Network enforcement

Materialize a narrow network policy, prove an allowed destination succeeds, prove a denied destination fails, and bind the enforcement evidence to the same execution.

### WNB-6 ETW completeness accounting

Capture under pressure. Record ETW loss counters. A run with unaccounted or non-zero loss is not allowed to make a stronger completeness claim than the evidence permits.

### WNB-7 Teardown

Prove compute-system/process/job/network objects are absent or terminal after destroy and that stale handles/identifiers cannot be reused to widen authority.

### WNB-8 Consequence finality separation

Demonstrate that Windows substrate completion is not accepted as target-side consequence success. Force response loss after target commit and require `OUTCOME_UNKNOWN` until server-bound reconciliation resolves finality.

### WNB-9 Evidence tamper test

Modify a retained native event/evidence record after collection and require independent verification to return INVALID or INDETERMINATE rather than VALID.

### WNB-10 Hardware-rooted provenance

Optional higher assurance: challenge a TPM-backed attestation with a fresh nonce and verify the returned quote/PCR/signature chain. Until this passes, the binding MUST NOT be described as hardware-attested.

## 10. Result vocabulary

Each WNB test returns one of:

- `VALID` - required native bindings/enforcement were directly measured and independently verified.
- `INVALID` - a falsifier occurred or the claimed binding/enforcement is contradicted by evidence.
- `INDETERMINATE` - required observer, privilege, event completeness, or trust evidence was unavailable.

`PARTIAL` may be used in engineering notes but MUST resolve to one of the three proof verdicts for a sealed evidence packet.

## 11. Implementation order

1. Read-only collector for HCS/HNS/HCN/ETW/process/token/Job Object/WFP state.
2. Parser and native-object graph joiner.
3. Independent binding verifier and `binding_digest` generation.
4. WindowsVreAdapter implementation behind the existing CAPPO consequence path.
5. Job Object/HCS resource projection and hostile enforcement tests.
6. Network-authority projection and hostile egress tests.
7. PGL evidence sink preserving raw-record digests, event-loss state, and observer metadata.
8. Target-side reconciliation integration.
9. Optional TPM/VBS attestation profile.

## 12. Claim discipline

Permitted after WNB-1/WNB-2:

> Windows exposes native workload, compute, and network identities that Veklom can bind into an execution graph.

Permitted only after WNB-4/WNB-5:

> The Windows VRE adapter projects approved resource and network bounds into native Windows enforcement and verifies the effective result.

Permitted only after WNB-6/WNB-9 and the applicable PGL verifier requirements:

> Windows-native execution evidence is captured with measured completeness and tamper detection.

Permitted only after WNB-10:

> The Windows execution binding is hardware-attested under the verified TPM/VBS profile.

Not currently permitted:

- `Windows is already a consequence-governed substrate.`
- `VM SID is a cryptographic identity.`
- `ETW is the native PGL spine.`
- `kernel-verified consequence finality.`
- `zero-friction.`
- `zero-agent` as a universal deployment claim.

## 13. Design conclusion

The Windows opportunity is not to replace Windows primitives. It is to make native Windows state an enforceable, verifiable projection of a Veklom consequence contract while preserving the distinction between authority, substrate execution, observation, evidence, and target-side finality.
