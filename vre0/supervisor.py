#!/usr/bin/env python3
"""
VRE-0 Supervisor
================
Runs four adversarial scenarios against a signed execution envelope inside a
real cgroup v2 boundary delegated by systemd --user. Emits a per-scenario
TransitionReceipt and a summary matrix.

Architecture:
  Envelope (signed contract)
      → cgroup boundary (materialised, verified)
          → Wasmtime execution (fuel-metered, wall-deadline guarded)
              → substrate evidence (cgroup fields, exit code, OOM events)
                  → TransitionReceipt (envelope_digest + cgroup_path + outcome)

The four scenarios and their required discriminators:
  ram_bomb      → oom_kill_delta > 0           (ENVELOPE_MEMORY_EXCEEDED)
  spin_fuel     → wasmtime fuel trap           (ENVELOPE_COMPUTE_EXHAUSTED)
  deadline_stall → wall deadline exceeded      (ENVELOPE_DEADLINE_EXCEEDED)
  normal_ok     → completed, exit 0            (COMPLETED)

Usage (from inside a systemd --user delegated unit):
  /home/antho/vre0-venv/bin/python3 vre0/supervisor.py
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Import from sibling envelope.py
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))  # cappo-backend root

from vre0.envelope import (
    ExecutionEnvelope,
    OutcomeCode,
    SubstrateEvidence,
    TransitionReceipt,
)

# ---------------------------------------------------------------------------
# Wasmtime import
# ---------------------------------------------------------------------------
try:
    from wasmtime import (
        Config,
        Engine,
        FuncType,
        Instance,
        Linker,
        Module,
        Store,
    )
    WASMTIME_AVAILABLE = True
except ImportError:
    WASMTIME_AVAILABLE = False

# ---------------------------------------------------------------------------
# SCENARIO REGISTRY
# ---------------------------------------------------------------------------

WAT_DIR = _HERE / "wasm_modules"


# ---------------------------------------------------------------------------
# CGROUP HELPERS
# ---------------------------------------------------------------------------

def _cg_read(p: pathlib.Path) -> str:
    try:
        return p.read_text().strip()
    except Exception as e:
        return f"ERROR:{e}"


def _cg_write(p: pathlib.Path, value: str) -> bool:
    try:
        p.write_text(value)
        return True
    except Exception as e:
        print(f"  [cg_write] FAIL {p}: {e}", file=sys.stderr)
        return False


def _read_memory_events(cg: pathlib.Path) -> dict:
    raw = _cg_read(cg / "memory.events")
    result: dict = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                result[parts[0]] = parts[1]
    return result


# ---------------------------------------------------------------------------
# SUPERVISOR TOPOLOGY SETUP
# ---------------------------------------------------------------------------

class VRE0Topology:
    """
    Establishes the cgroup hierarchy inside the current delegated unit.

      unit_root/
        supervisor/      <- this process lives here
        executions/
          <exec-uuid>/   <- one per scenario run
    """

    def __init__(self) -> None:
        self.unit_root: Optional[pathlib.Path] = None
        self.supervisor_cg: Optional[pathlib.Path] = None
        self.executions_cg: Optional[pathlib.Path] = None

    def setup(self) -> str:
        raw = pathlib.Path("/proc/self/cgroup").read_text().strip()
        cg_rel: Optional[str] = None
        for line in raw.splitlines():
            parts = line.split(":", 2)
            if parts[0] == "0" and len(parts) == 3:
                cg_rel = parts[2]
                break
        if not cg_rel:
            raise RuntimeError("No cgroup v2 entry in /proc/self/cgroup")

        self.unit_root = pathlib.Path(f"/sys/fs/cgroup{cg_rel}")
        print(f"[topology] unit_root = {self.unit_root}")

        self.supervisor_cg = self.unit_root / "supervisor"
        self.executions_cg = self.unit_root / "executions"
        self.supervisor_cg.mkdir(exist_ok=True)
        self.executions_cg.mkdir(exist_ok=True)

        pid = os.getpid()
        if not _cg_write(self.supervisor_cg / "cgroup.procs", str(pid)):
            raise RuntimeError("Failed to migrate supervisor PID to supervisor/ leaf")

        procs_in_unit = _cg_read(self.unit_root / "cgroup.procs")
        if str(pid) in procs_in_unit.splitlines():
            raise RuntimeError("PID still in unit root after migration attempt")

        if not _cg_write(self.unit_root / "cgroup.subtree_control", "+memory +pids"):
            raise RuntimeError("Failed to enable +memory +pids in unit root")

        if not _cg_write(self.executions_cg / "cgroup.subtree_control", "+memory +pids"):
            raise RuntimeError("Failed to enable +memory +pids in executions/")

        print(f"[topology] subtree_control: "
              f"{_cg_read(self.unit_root / 'cgroup.subtree_control')}")
        return str(self.unit_root)

    def create_execution_cgroup(
        self, exec_uuid: str, memory_max_bytes: int
    ) -> pathlib.Path:
        leaf = self.executions_cg / exec_uuid
        leaf.mkdir()
        if not _cg_write(leaf / "memory.max", str(memory_max_bytes)):
            raise RuntimeError(f"Failed to write memory.max={memory_max_bytes}")
        reread = _cg_read(leaf / "memory.max")
        try:
            if int(reread) != memory_max_bytes:
                raise RuntimeError(
                    f"memory.max binding mismatch: wrote={memory_max_bytes} reread={reread}"
                )
        except ValueError:
            raise RuntimeError(f"memory.max non-integer: {reread!r}")
        return leaf

    def cleanup_execution_cgroup(self, leaf: pathlib.Path) -> None:
        try:
            leaf.rmdir()
        except Exception as e:
            print(f"  [cleanup] could not rmdir {leaf}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# WASMTIME RUNNER
# ---------------------------------------------------------------------------


def _run_wasmtime(
    scenario: str,
    wat_path: pathlib.Path,
    fuel_budget: int,
    wall_deadline_seconds: float,
) -> tuple[OutcomeCode, str, Optional[str]]:
    """
    Run a WAT module with fuel metering + wall-clock deadline.

    Deadline detection: wasmtime execution runs in a daemon thread.
    Main thread waits up to wall_deadline_seconds. If the thread hasn't
    finished, deadline is declared. No SIGALRM — that kills the process.

    Returns: (OutcomeCode, evidence_source, fuel_trap_reason|None)
    """
    cfg = Config()
    cfg.consume_fuel = True
    engine = Engine(cfg)
    store = Store(engine)
    store.set_fuel(fuel_budget)

    if scenario == "ram_bomb":
        raise RuntimeError("ram_bomb uses subprocess path, not _run_wasmtime")

    # --- DEADLINE STALL: WAT calls imported host_sleep ---
    if scenario == "deadline_stall":
        # The host_sleep will block for 3× the deadline, but the main thread
        # will declare the deadline before waiting that long.
        _sleep_s = wall_deadline_seconds * 3

        def _host_sleep() -> None:
            time.sleep(_sleep_s)

        linker = Linker(engine)
        linker.define_func(
            "env",
            "host_sleep",
            FuncType([], []),
            lambda: _host_sleep(),
        )
        module = Module(engine, wat_path.read_bytes())
        instance = linker.instantiate(store, module)
        run_fn = instance.exports(store)["run"]
    else:
        module = Module(engine, wat_path.read_bytes())
        instance = Instance(store, module, [])
        run_fn = instance.exports(store)["run"]

    # Result container shared between main and wasm thread
    result: dict = {"done": False, "exc": None, "exc_str": "", "exc_type": ""}

    def _run_wasm() -> None:
        try:
            run_fn(store)
            result["done"] = True
        except Exception as exc:
            result["done"] = True
            result["exc"] = exc
            result["exc_str"] = str(exc)
            result["exc_type"] = type(exc).__name__

    thread = threading.Thread(target=_run_wasm, daemon=True)
    thread.start()
    thread.join(timeout=wall_deadline_seconds)

    outcome: OutcomeCode = OutcomeCode.INTERNAL_ERROR
    evidence_source = "unknown"
    fuel_trap_reason: Optional[str] = None

    if thread.is_alive():
        # Deadline fired — thread is still blocked (e.g., host_sleep)
        outcome = OutcomeCode.ENVELOPE_DEADLINE_EXCEEDED
        evidence_source = "wall_deadline_timer"
        # Thread is daemon — it will be abandoned and GC'd
    else:
        exc = result["exc"]
        if exc is None:
            outcome = OutcomeCode.COMPLETED
            evidence_source = "wasmtime_normal_return"
        else:
            exc_str = result["exc_str"]
            exc_type = result["exc_type"]
            if "fuel" in exc_str.lower() or "out of fuel" in exc_str.lower():
                outcome = OutcomeCode.ENVELOPE_COMPUTE_EXHAUSTED
                evidence_source = "wasmtime_fuel_trap"
                fuel_trap_reason = exc_str
            elif "trap" in exc_type.lower() or "trap" in exc_str.lower():
                outcome = OutcomeCode.INTERNAL_ERROR
                evidence_source = "wasmtime_trap"
                fuel_trap_reason = exc_str
            else:
                outcome = OutcomeCode.INTERNAL_ERROR
                evidence_source = f"{exc_type}: {exc_str[:120]}"

    return outcome, evidence_source, fuel_trap_reason




# ---------------------------------------------------------------------------
# SCENARIO RUNNER
# ---------------------------------------------------------------------------

def run_scenario(
    scenario: str,
    env: ExecutionEnvelope,
    topology: VRE0Topology,
) -> TransitionReceipt:
    wat_map = {
        "ram_bomb":       "ram_bomb.wat",
        "spin_fuel":      "spin_forever.wat",
        "deadline_stall": "sleep_or_stall.wat",
        "normal_ok":      "normal_ok.wat",
    }
    wat_path = WAT_DIR / wat_map[scenario]
    sup_pid = os.getpid()  # supervisor's own PID

    print(f"\n{'='*60}")
    print(f"  SCENARIO: {scenario}")
    print(f"  envelope_id:     {env.envelope_id}")
    print(f"  envelope_digest: {env.digest()}")
    print(f"  memory.max:      {env.memory_max_bytes} ({env.memory_max_bytes//1048576} MiB)")
    print(f"  fuel_budget:     {env.fuel_budget}")
    print(f"  wall_deadline:   {env.wall_deadline_seconds}s")
    print(f"{'='*60}")

    ev = SubstrateEvidence(
        execution_uuid=env.envelope_id,
        host_pid=sup_pid,
        materialization_ts=time.time(),
    )

    # Create cgroup leaf + bind memory.max
    exec_cg = topology.create_execution_cgroup(env.envelope_id, env.memory_max_bytes)
    ev.cgroup_path = str(exec_cg)
    ev.configured_memory_max_bytes = int(_cg_read(exec_cg / "memory.max"))
    ev.configured_fuel_budget = env.fuel_budget

    binding_verified = ev.configured_memory_max_bytes == env.memory_max_bytes
    binding_failure = (
        None
        if binding_verified
        else f"configured={ev.configured_memory_max_bytes} envelope={env.memory_max_bytes}"
    )
    print(f"  [binding] ok={binding_verified}  "
          f"configured={ev.configured_memory_max_bytes}  "
          f"envelope={env.memory_max_bytes}")

    # Baseline memory.events
    baseline_events = _read_memory_events(exec_cg)
    print(f"  [events baseline] {baseline_events}")

    # ── ram_bomb: out-of-process subprocess ──────────────────────────────────
    # OOM kills the process that caused it. The supervisor must NOT be in
    # exec_cg — the subprocess is placed there instead.
    if scenario == "ram_bomb":
        worker = _HERE / "ram_bomb_worker.py"
        python_exe = sys.executable
        proc = subprocess.Popen(
            [python_exe, str(worker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        child_pid = proc.pid
        ev.pid_tree = [child_pid]
        print(f"  [subprocess] child PID {child_pid} spawned")

        # Place child PID in execution cgroup BEFORE it starts allocating
        pid_placed = _cg_write(exec_cg / "cgroup.procs", str(child_pid))
        if not pid_placed:
            proc.kill()
            proc.wait()
            ev.termination_ts = time.time()
            topology.cleanup_execution_cgroup(exec_cg)
            return _build_receipt(
                env, ev, OutcomeCode.INTERNAL_ERROR,
                "child_pid_placement_failed", binding_verified, binding_failure, True
            )
        print(f"  [placement] child {child_pid} confirmed: "
              f"{_cg_read(exec_cg / 'cgroup.procs')}")

        # Wait for child (killed by OOM or completes)
        try:
            proc.wait(timeout=env.wall_deadline_seconds + 5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        ev.exit_code = proc.returncode
        ev.termination_ts = time.time()

        post_events = _read_memory_events(exec_cg)
        oom_kill_delta = (
            post_events.get("oom_kill", 0) - baseline_events.get("oom_kill", 0)
        )
        oom_event_delta = (
            post_events.get("oom", 0) - baseline_events.get("oom", 0)
        )
        try:
            ev.peak_memory_bytes = int(_cg_read(exec_cg / "memory.peak"))
        except Exception:
            ev.peak_memory_bytes = None
        ev.memory_max_at_termination = ev.configured_memory_max_bytes
        ev.oom_killed = oom_kill_delta > 0

        # exit code -9 / SIGKILL = OOM kill; also check oom_kill_delta
        if oom_kill_delta > 0 or proc.returncode == -9:
            outcome = OutcomeCode.ENVELOPE_MEMORY_EXCEEDED
            evidence_source = (
                f"oom_kill_delta={oom_kill_delta} rc={proc.returncode} cgroup memory.events"
            )
        else:
            outcome = OutcomeCode.INTERNAL_ERROR
            evidence_source = f"unexpected_exit rc={proc.returncode}"

        print(f"  [outcome]          {outcome.value}")
        print(f"  [evidence_source]  {evidence_source}")
        print(f"  [oom_kill_delta]   {oom_kill_delta}")
        print(f"  [peak_memory]      {ev.peak_memory_bytes}")
        print(f"  [child_exit_code]  {proc.returncode}")

        topology.cleanup_execution_cgroup(exec_cg)
        receipt = _build_receipt(
            env, ev, outcome, evidence_source,
            binding_verified, binding_failure, host_survived=True
        )
        receipt._extras = {  # type: ignore[attr-defined]
            "oom_kill_delta": oom_kill_delta,
            "oom_event_delta": oom_event_delta,
            "baseline_events": baseline_events,
            "post_events": post_events,
        }
        return receipt

    # ── all other scenarios: in-process with supervisor PID ──────────────────
    pid_placed = _cg_write(exec_cg / "cgroup.procs", str(sup_pid))
    if not pid_placed:
        ev.termination_ts = time.time()
        topology.cleanup_execution_cgroup(exec_cg)
        return _build_receipt(
            env, ev, OutcomeCode.INTERNAL_ERROR,
            "pid_placement_failed", binding_verified, binding_failure, False
        )

    ev.pid_tree = [sup_pid]
    print(f"  [placement] supervisor {sup_pid} confirmed: "
          f"{_cg_read(exec_cg / 'cgroup.procs')}")

    # Run Wasmtime
    if WASMTIME_AVAILABLE:
        outcome, evidence_source, fuel_trap_reason = _run_wasmtime(
            scenario, wat_path, env.fuel_budget, env.wall_deadline_seconds
        )
    else:
        outcome = OutcomeCode.INTERNAL_ERROR
        evidence_source = "wasmtime_not_available"
        fuel_trap_reason = None

    ev.fuel_trap_reason = fuel_trap_reason
    ev.deadline_exceeded = outcome == OutcomeCode.ENVELOPE_DEADLINE_EXCEEDED

    # Migrate supervisor PID back to supervisor/ leaf
    _cg_write(topology.supervisor_cg / "cgroup.procs", str(sup_pid))
    ev.termination_ts = time.time()

    # Post-run memory.events
    post_events = _read_memory_events(exec_cg)
    print(f"  [events post]     {post_events}")

    oom_kill_delta = (
        post_events.get("oom_kill", 0) - baseline_events.get("oom_kill", 0)
    )
    oom_event_delta = (
        post_events.get("oom", 0) - baseline_events.get("oom", 0)
    )

    try:
        ev.peak_memory_bytes = int(_cg_read(exec_cg / "memory.peak"))
    except Exception:
        ev.peak_memory_bytes = None
    try:
        ev.memory_max_at_termination = int(_cg_read(exec_cg / "memory.max"))
    except Exception:
        ev.memory_max_at_termination = None

    ev.exit_code = 0 if outcome == OutcomeCode.COMPLETED else -1
    ev.oom_killed = oom_kill_delta > 0

    # Upgrade INTERNAL_ERROR/wasmtime_trap to ENVELOPE_MEMORY_EXCEEDED if OOM observed
    if oom_kill_delta > 0 and outcome not in (
        OutcomeCode.ENVELOPE_COMPUTE_EXHAUSTED,
        OutcomeCode.ENVELOPE_DEADLINE_EXCEEDED,
    ):
        outcome = OutcomeCode.ENVELOPE_MEMORY_EXCEEDED
        evidence_source = f"oom_kill_delta={oom_kill_delta} cgroup memory.events"

    print(f"  [outcome]          {outcome.value}")
    print(f"  [evidence_source]  {evidence_source}")
    print(f"  [oom_kill_delta]   {oom_kill_delta}")
    print(f"  [peak_memory]      {ev.peak_memory_bytes}")

    topology.cleanup_execution_cgroup(exec_cg)

    receipt = _build_receipt(
        env, ev, outcome, evidence_source,
        binding_verified, binding_failure, host_survived=True
    )
    receipt._extras = {  # type: ignore[attr-defined]
        "oom_kill_delta": oom_kill_delta,
        "oom_event_delta": oom_event_delta,
        "baseline_events": baseline_events,
        "post_events": post_events,
    }
    return receipt



def _build_receipt(
    env: ExecutionEnvelope,
    ev: SubstrateEvidence,
    outcome: OutcomeCode,
    evidence_source: str,
    binding_verified: bool,
    binding_failure: Optional[str],
    host_survived: bool,
) -> TransitionReceipt:
    return TransitionReceipt(
        envelope_digest=env.digest(),
        execution_uuid=ev.execution_uuid,
        scenario=env.scenario,
        cgroup_path=ev.cgroup_path,
        host_pid=ev.host_pid,
        configured_memory_max_bytes=ev.configured_memory_max_bytes,
        configured_fuel_budget=ev.configured_fuel_budget,
        envelope_memory_max_bytes=env.memory_max_bytes,
        envelope_fuel_budget=env.fuel_budget,
        binding_verified=binding_verified,
        binding_failure_reason=binding_failure,
        host_survived=host_survived,
        workload_contained=outcome != OutcomeCode.INTERNAL_ERROR,
        outcome=outcome,
        outcome_evidence_source=evidence_source,
        evidence=ev,
    )


# ---------------------------------------------------------------------------
# MATRIX
# ---------------------------------------------------------------------------

EXPECTED_OUTCOMES: dict[str, OutcomeCode] = {
    "ram_bomb":       OutcomeCode.ENVELOPE_MEMORY_EXCEEDED,
    "spin_fuel":      OutcomeCode.ENVELOPE_COMPUTE_EXHAUSTED,
    "deadline_stall": OutcomeCode.ENVELOPE_DEADLINE_EXCEEDED,
    "normal_ok":      OutcomeCode.COMPLETED,
}


def print_matrix(receipts: list[TransitionReceipt]) -> bool:
    print(f"\n{'='*80}")
    print("VRE-0 SCENARIO MATRIX")
    print(f"{'='*80}")
    hdr = (
        f"  {'Scenario':<18} {'Expected':<28} {'Actual':<28} "
        f"{'bind':<6} {'oom_Δ':<8} {'VERDICT'}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    all_pass = True
    for r in receipts:
        expected = EXPECTED_OUTCOMES[r.scenario]
        matched = r.outcome == expected
        if not matched:
            all_pass = False
        extras = getattr(r, "_extras", {})
        oom_d = extras.get("oom_kill_delta", "n/a")
        verdict = "PASS" if matched else "FAIL"
        mark = "+" if matched else "!"
        print(
            f"  {mark} {r.scenario:<17} {expected.value:<28} {r.outcome.value:<28} "
            f"{'Y' if r.binding_verified else 'N':<6} {str(oom_d):<8} {verdict}"
        )

    print(f"\n  DISCRIMINATORS (raw classification fields):")
    for r in receipts:
        ev = r.evidence
        extras = getattr(r, "_extras", {})
        print(
            f"    {r.scenario:<18} "
            f"oom_kill_d={extras.get('oom_kill_delta','?'):<4}  "
            f"fuel_trap={'yes' if ev and ev.fuel_trap_reason else 'no':<4}  "
            f"deadline={ev.deadline_exceeded if ev else '?'}  "
            f"rc={ev.exit_code if ev else '?'}  "
            f"mem.max={r.configured_memory_max_bytes}"
        )

    print(f"\n{'='*80}")
    print(f"  RESULT: {'VRE-0 ALL PASS' if all_pass else 'VRE-0 PARTIAL FAIL'}")
    print(f"{'='*80}")
    return all_pass


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    if not WASMTIME_AVAILABLE:
        print("FATAL: wasmtime not available. pip install wasmtime", file=sys.stderr)
        sys.exit(1)

    for fn in ("ram_bomb.wat", "spin_forever.wat", "sleep_or_stall.wat", "normal_ok.wat"):
        p = WAT_DIR / fn
        if not p.exists():
            print(f"FATAL: missing workload file: {p}", file=sys.stderr)
            sys.exit(1)

    topology = VRE0Topology()
    try:
        unit_root = topology.setup()
    except Exception as e:
        print(f"FATAL: topology setup failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[supervisor] Ready. unit_root={unit_root}\n")

    # Four scenario configurations
    configs = [
        # scenario,         mem_MiB, fuel,       deadline_s
        ("ram_bomb",        64,      50_000_000, 10.0),
        ("spin_fuel",       64,      50_000_000, 10.0),
        ("deadline_stall",  64,      50_000_000,  0.3),
        ("normal_ok",       64,      50_000_000,  5.0),
    ]

    receipts: list[TransitionReceipt] = []
    out_dir = _HERE / "receipts"
    out_dir.mkdir(exist_ok=True)

    for scenario, mem_mib, fuel, deadline in configs:
        env = ExecutionEnvelope(
            scenario=scenario,
            memory_max_bytes=mem_mib * 1024 * 1024,
            fuel_budget=fuel,
            wall_deadline_seconds=deadline,
        )
        receipt = run_scenario(scenario, env, topology)
        receipts.append(receipt)
        p = out_dir / f"{scenario}.json"
        p.write_text(receipt.to_json())
        print(f"  [saved] {p}")

    all_pass = print_matrix(receipts)

    matrix = {
        r.scenario: {
            "outcome": r.outcome.value,
            "expected": EXPECTED_OUTCOMES[r.scenario].value,
            "passed": r.outcome == EXPECTED_OUTCOMES[r.scenario],
            "binding_verified": r.binding_verified,
            "oom_kill_delta": getattr(r, "_extras", {}).get("oom_kill_delta"),
            "evidence_source": r.outcome_evidence_source,
            "configured_memory_max": r.configured_memory_max_bytes,
            "envelope_digest": r.envelope_digest,
            "cgroup_path": r.cgroup_path,
        }
        for r in receipts
    }
    mp = out_dir / "matrix.json"
    mp.write_text(json.dumps(matrix, indent=2))
    print(f"[supervisor] matrix.json → {mp}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
