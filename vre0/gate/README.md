# VRE-0 gate — hostile resource boundary

VRE-0 is the first gate in the VRE track: show locally that one signed contract
binds authority, a physical resource boundary, the execution that ran inside
it, and the receipt that reports the result, with nothing downstream widening
what the contract allowed. This directory has the harness, a verifier built
separately from it, a tamper suite, and one committed run.

**Status: VRE-0A/0B/0C pass on one host, at evidence level E2, on cgroup v1.**
This is not a general claim about other kernels, cgroup v2, or other hosts
until it is re-run there. The limits are listed below.

## Run it

```bash
pip install -r vre0/gate/requirements.txt
sudo python3 -m vre0.gate.harness --iterations 20      # writes vre0/gate/runs/<run_id>/
python3 -m vre0.gate.verify vre0/gate/runs/<run_id> --trust trust.json
python3 -m vre0.gate.tamper vre0/gate/runs/<run_id>
pytest tests/test_vre0_gate.py                          # re-verifies the committed run, keys pinned
```

The harness needs root and a cgroup memory controller. The verifier and the
tamper suite need neither: they read stored files only.

## The chain

| Record | Written by | Commits to | Holds |
|---|---|---|---|
| Envelope **E** | issuer (Ed25519) | — | execution id, module sha256, `memory_max_bytes`, `compute_fuel`, `wall_deadline_ms`, `output_max_bytes`, validity window |
| Boundary **B** | supervisor | digest(E) | cgroup paths + inodes, limits **read back from the kernel**, PID + start time, `/proc/<pid>/cgroup` membership, module digest and fuel the runtime actually set, recomputable checks |
| Evidence **X** | supervisor | digest(B) | cgroup counters before/after, `waitpid` status, supervisor kill actions (pidfd), runtime report, untrusted guest output, teardown |
| Receipt **R** | executor (Ed25519) | digest(E), digest(B), digest(X) | outcome |

Order of operations for one execution (`harness.py`):

1. **Authorize.** Check the issuer signature against trusted keys and the
   validity window. On failure, nothing is materialized.
2. **Materialize.** Create the cgroup and write the limits. On v1, memsw is
   bounded to the same value so swap cannot widen it. Spawn the runtime shim,
   which writes its own PID into the cgroup *before exec*.
3. **Bind.** The shim compiles the guest, sets fuel, and reports READY, then
   blocks on a gate. The supervisor reads limits back from the kernel, checks
   the PID's membership from `/proc`, and checks the module digest and fuel
   against E. Any widening or mismatch refuses the execution before the guest
   runs.
4. **Release.** Open the gate and start the wall deadline.
5. **Enforce.** The kernel enforces memory (OOM kill) and the runtime enforces
   fuel and output. The supervisor sends SIGKILL through a pidfd at the
   deadline.
6. **Observe and receipt.** Read counters, the wait status, the runtime
   report, and residual processes, then tear down. Classify from that evidence
   alone and sign.

## Outcome rule

The harness's `classify()` and the verifier's `outcome_from_evidence()` are
written separately. They must agree on every stored record, and a test checks
that they do.

| Outcome | Requires (all of) |
|---|---|
| `ENVELOPE_MEMORY_EXCEEDED` | cgroup `oom_kill` delta ≥ 1, killed by SIGKILL, no supervisor kill, no runtime report |
| `ENVELOPE_DEADLINE_EXCEEDED` | supervisor deadline SIGKILL delivered, killed by SIGKILL, `oom_kill` delta 0, no runtime report |
| `ENVELOPE_COMPUTE_EXHAUSTED` | exit 3, runtime report `trap=fuel` with `fuel_remaining=0`, no OOM, no kill |
| `ENVELOPE_OUTPUT_EXCEEDED` | exit 5, runtime report `trap=output_limit`, no OOM, no kill |
| `COMPLETED` | exit 0, runtime report `status=returned`, no OOM, no kill |
| `AUTHORITY_REFUSED` / `BINDING_REFUSED` | refusal recorded at that stage, gate never opened |
| `INDETERMINATE` | anything else: the rule never guesses |

Guest output (`untrusted_guest_output`) is recorded and never read by either rule.

## Committed run: `runs/20261011T035711Z`

Host: Firecracker VM, Linux 6.18.44, 4 vCPU, 16 GiB, no swap, **cgroup v1**
(memory + cpuacct). Python 3.13.16, wasmtime-py 44.0.0, cryptography 50.0.2.
20 iterations × 11 scenarios = 220 attacker executions, each paired with a
sibling, plus one calibration sibling.
`verify`: **VALID, 8,858 checks, 0 failures.** `tamper`: 16 of 16 forgeries
rejected (one known limit, below).

Attacker envelope (from the brief): 64 MiB memory, 50,000,000 fuel, 100 ms
deadline, 4096 B output. The memory bombs use a 2000 ms deadline; see
*Findings*. The sibling envelope is 128 MiB.

| Scenario | Outcome (20/20) | Attacker elapsed p50 / p95 ms | Attacker peak |
|---|---|---|---|
| normal_ok | COMPLETED | 26.4 / 36.5 | 19.2 MiB |
| ram_bomb (touches up to 192 MiB) | ENVELOPE_MEMORY_EXCEEDED | 187.6 / 293.3 | 64.0 MiB |
| lying_bomb (prints "COMPLETED ok", then bombs) | ENVELOPE_MEMORY_EXCEEDED | 182.4 / 200.6 | 64.0 MiB |
| spin_fuel | ENVELOPE_COMPUTE_EXHAUSTED | 34.8 / 50.3 | 11.2 MiB |
| deadline_stall (blocks in a host call) | ENVELOPE_DEADLINE_EXCEEDED | 105.1 / 108.4 (kill→reap 5.1 / 8.3) | 11.2 MiB |
| output_flood | ENVELOPE_OUTPUT_EXCEEDED | 20.7 / 30.4 | 11.2 MiB |
| expired_authority | AUTHORITY_REFUSED | — | — |
| forged_authority (unknown issuer key) | AUTHORITY_REFUSED | — | — |
| widened_boundary (supervisor writes 512 MiB) | BINDING_REFUSED | — | — |
| substituted_module (launches ram_bomb under a normal_ok envelope) | BINDING_REFUSED | — | — |
| unplaced_process (shim not put in the cgroup) | BINDING_REFUSED | — | — |

Peak memory includes the runtime itself, Python plus wasmtime, which is about
11 MiB charged after placement. So the guest's usable share of 64 MiB is
smaller than 64 MiB.

Containment evidence, checked per pair:
- The sibling, in its own cgroup, was running for the attacker's **whole**
  lifetime: released first, reaped last.
- The sibling completed with the exact value fixed by a solo calibration run.
- The sibling's cgroup recorded 0 OOM kills.
- The supervisor's own cgroup recorded 0 OOM kills.
- A host canary process ran successfully after every scenario.
- No processes remained in any boundary.
- Every boundary was removed.

## What VRE-0 shows here, and what it does not

**0A Containment.** Each hostile workload was stopped by the bound it attacked.
The host and the sibling were unaffected (evidence above).

**0B Binding.** The receipt commits to a boundary record whose limits were read
back from the kernel and whose process was observed in that cgroup. A widened
limit, a substituted module, or an unplaced process each refused execution
before the guest ran. The verifier recomputes these checks from raw fields
rather than trusting the recorded ones. A record transplanted from another
cgroup fails, as does a membership line pointing at another cgroup.

**0C Evidence continuity.** E → B → X → R are hash-linked and signed at both
ends. The outcome is re-derived from substrate evidence by an independent
rule. A guest's false success claim is on record and ignored.

**Limits. Do not drop these when quoting results.**
- **Evidence level E2.** The supervisor is inside the trust domain. If it lies
  consistently across every record and the run's stated expectations, the
  verifier cannot catch it. The tamper suite's last case demonstrates this and
  is reported as accepted. Closing this gap needs an independent observer
  (E3), for example the eBPF sidecar from `substrate_proofs_linux`
  (LINUX-EVIDENCE-1), joined on cgroup inode.
- **Kernel enforcement is not a mathematical guarantee.** Memory is enforced by
  reclaim and the OOM killer. Usage can sit at or briefly above the limit
  before the kill lands. Here the recorded peak equals the limit exactly.
- **Fuel and output bounds are enforced by the runtime, not the kernel.** The
  shim (wasmtime) is in the TCB; the guest is not. Exit codes 3 and 5 come
  from that shim.
- **cgroup v2 path not exercised.** `CgroupV2` in `substrate.py` follows the
  kernel interface (`memory.max`, `memory.swap.max=0`, `memory.oom.group`,
  `memory.events`), but this host binds the memory controller to v1. Before
  claiming v2, run the harness on a v2 host (for example the WSL2 box). The
  binding checks refuse release on a bad binding rather than pass it.
- **One host, one run.** The timings above are this VM's numbers, not
  universal ones.
- **Not covered:** fork bombs (no `pids` bound in the envelope yet), network,
  disk I/O, and CPU-share isolation. The sibling's completion shows it was not
  starved past its 5 s deadline. It does not show fairness.
- **Keys are per run** and only public keys are stored. The trust root here
  is the pinned key IDs in `tests/test_vre0_gate.py`, not a CAPPO issuer.

## Findings from building this

1. **The brief's 100 ms deadline never let the memory bound bind.** In the
   first run, ram_bomb and lying_bomb were killed by the deadline at a 34 MiB
   peak with `oom_kill=0`. On this VM, pages fault in at roughly 0.2 MiB/ms
   under memcg charging. The receipts correctly said
   `ENVELOPE_DEADLINE_EXCEEDED`; the expectation was wrong. That run is
   preserved in `failures/20261011T035046Z-ram-bomb-hits-100ms-deadline/`. It
   was produced by an earlier harness revision whose evidence fields differ,
   so the current verifier does not read it. The memory-bomb envelopes now use
   a 2000 ms deadline. Whichever bound is tightest binds first, and a test of
   one bound has to make that bound the tightest.
2. **wasmtime-py 36 and 44 cannot name the out-of-fuel trap.** Reading
   `Trap.trap_code` raises `ValueError` (codes 11/12 are missing from the
   enum). The shim classifies fuel exhaustion from runtime state
   (`store.get_fuel() == 0` after a trap) instead of matching message text.
3. **The v1 fail counters stay at 0 across real OOM kills** on 6.18
   (`memory.failcnt`, `memory.memsw.failcnt`). They are recorded as reported
   and nothing classifies on them.
4. **The sibling first finished before the bombs were killed.** It needs to
   outlive the attacker, and the verifier now requires that, not just overlap.

## Relation to the earlier `vre0/supervisor.py` run

The receipts in `vre0/receipts/` (WSL2, cgroup v2) predate this harness. Read
against the brief, that run does not meet VRE-0:

- The envelope is hashed but not signed (`biscuit_token` is empty), so the
  "signed contract" is decorative.
- spin_fuel, deadline_stall and normal_ok run inside the supervisor process,
  after moving the supervisor's own PID into the execution cgroup. The
  supervisor shares the boundary it is supposed to enforce.
- The deadline abandons a daemon thread that keeps running. Nothing is
  terminated.
- For those scenarios, `exit_code` is synthesized (`0 if COMPLETED else -1`),
  not observed.
- ram_bomb is a Python worker, not the Wasm module; `ram_bomb.wat` stores
  one byte past the end of memory and would trap before allocating. The
  worker is placed in the cgroup after `Popen` returns, so it races the
  allocation. `rc == -9` alone was accepted as a memory violation.
- The verifier re-reads the supervisor's own fields, so it cannot disagree
  with them.

Those files are left in place as history. This directory supersedes them for
VRE-0.

## Files

- `contract.py`: canonical JSON, digests, Ed25519 sign/verify
- `substrate.py`: cgroup v1/v2 backends; every value comes from the kernel
- `worker.py`: runtime shim (TCB) that runs the untrusted Wasm guest
- `harness.py`: supervisor and scenarios; writes a run
- `verify.py`: independent verifier (stdlib + `cryptography` only)
- `tamper.py`: forged-artifact suite
- `workloads/*.wat`: guest modules
- `runs/`: committed run
- `failures/`: preserved failed run
