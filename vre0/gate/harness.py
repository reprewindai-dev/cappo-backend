#!/usr/bin/env python3
"""VRE-0 hostile-boundary harness (VRE-0A containment, 0B binding, 0C evidence).

Run as root on a Linux host with a cgroup memory controller (v1 or v2):

    python3 -m vre0.gate.harness --iterations 10

Each scenario pairs one attacker execution with one sibling execution that
runs at the same time in its own cgroup. Per execution the supervisor:

  1. authorizes:   verifies the issuer's Ed25519 signature and expiry,
  2. materializes: creates the cgroup, writes the limits, spawns the runtime
                   shim already placed in the cgroup (before exec),
  3. binds:        reads limits back from the kernel, checks the shim's PID is
                   a member, checks module digest and fuel; any widening or
                   mismatch refuses the execution before the guest runs,
  4. releases:     opens the start gate, starts the wall deadline,
  5. enforces:     SIGKILL through a pidfd at the deadline,
  6. observes:     cgroup counters, wait status, runtime report, teardown,
  7. receipts:     classifies from that evidence alone and signs a receipt.

Artifacts go to vre0/gate/runs/<run_id>/. Private keys are never written.
Verify a run with: python3 -m vre0.gate.verify vre0/gate/runs/<run_id>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import select
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vre0.gate import contract
from vre0.gate.substrate import Substrate, host_facts, mem_available_kib, proc_identity

HERE = pathlib.Path(__file__).resolve().parent
WORKLOADS = HERE / "workloads"
MiB = 1024 * 1024

# Brief, VRE-0A: memory.max 64 MiB, fixed fuel, 100 ms wall deadline, bounded output.
ATTACK_LIMITS = {"memory_max_bytes": 64 * MiB, "compute_fuel": 50_000_000,
                 "wall_deadline_ms": 100, "output_max_bytes": 4096}
SIBLING_LIMITS = {"memory_max_bytes": 128 * MiB, "compute_fuel": 20_000_000_000,
                  "wall_deadline_ms": 5000, "output_max_bytes": 4096}

# scenario -> (module the envelope authorizes, module actually launched, fault, expected)
SCENARIOS = {
    "normal_ok":          ("normal_ok", "normal_ok", None, "COMPLETED"),
    "ram_bomb":           ("ram_bomb", "ram_bomb", None, "ENVELOPE_MEMORY_EXCEEDED"),
    "spin_fuel":          ("spin_fuel", "spin_fuel", None, "ENVELOPE_COMPUTE_EXHAUSTED"),
    "deadline_stall":     ("deadline_stall", "deadline_stall", None, "ENVELOPE_DEADLINE_EXCEEDED"),
    "output_flood":       ("output_flood", "output_flood", None, "ENVELOPE_OUTPUT_EXCEEDED"),
    "lying_bomb":         ("lying_bomb", "lying_bomb", None, "ENVELOPE_MEMORY_EXCEEDED"),
    # Negative controls: the supervisor must refuse; the guest must never run.
    "expired_authority":  ("normal_ok", "normal_ok", "expired", "AUTHORITY_REFUSED"),
    "forged_authority":   ("normal_ok", "normal_ok", "forged", "AUTHORITY_REFUSED"),
    "widened_boundary":   ("normal_ok", "normal_ok", "widen_memory", "BINDING_REFUSED"),
    "substituted_module": ("normal_ok", "ram_bomb", None, "BINDING_REFUSED"),
    "unplaced_process":   ("normal_ok", "normal_ok", "skip_placement", "BINDING_REFUSED"),
}

# A memory bomb on the reference host faults in about 0.2 MiB/ms, so with the
# brief's 100 ms deadline it was killed by the deadline at a 34 MiB peak and the
# memory boundary was never exercised (preserved in failures/). Memory-bomb
# envelopes get a longer deadline so memory is the first bound to bind.
LIMIT_OVERRIDES = {
    "ram_bomb": {"wall_deadline_ms": 2000},
    "lying_bomb": {"wall_deadline_ms": 2000},
}

READY_TIMEOUT_S = 15


def now_ns() -> int:
    return time.monotonic_ns()


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Classification. verify.py carries its own copy of this rule on purpose; the
# two must agree on every stored execution.
# ---------------------------------------------------------------------------

def classify(ev: dict) -> str:
    if ev["refusal"] is not None:
        if ev["released"]:
            return "INDETERMINATE"
        return {"authority": "AUTHORITY_REFUSED", "binding": "BINDING_REFUSED"}.get(
            ev["refusal"]["stage"], "INDETERMINATE")
    if not ev["released"]:
        return "INDETERMINATE"
    oom = ev["counters_after"]["oom_kill"] - ev["counters_before"]["oom_kill"]
    deadline_kill = any(a["action"] == "SIGKILL" and a["reason"] == "wall_deadline"
                        and a["result"] == "delivered" for a in ev["supervisor_actions"])
    sig, code, rep = ev["wait_status"]["signal"], ev["wait_status"]["exit_code"], ev["runtime_report"]
    if oom >= 1 and sig == signal.SIGKILL and not deadline_kill and rep is None:
        return "ENVELOPE_MEMORY_EXCEEDED"
    if deadline_kill and sig == signal.SIGKILL and oom == 0 and rep is None:
        return "ENVELOPE_DEADLINE_EXCEEDED"
    if oom == 0 and not deadline_kill and sig is None and rep is not None:
        if code == 3 and rep.get("trap") == "fuel" and rep.get("fuel_remaining") == 0:
            return "ENVELOPE_COMPUTE_EXHAUSTED"
        if code == 5 and rep.get("trap") == "output_limit":
            return "ENVELOPE_OUTPUT_EXCEEDED"
        if code == 0 and rep.get("status") == "returned":
            return "COMPLETED"
    return "INDETERMINATE"


# ---------------------------------------------------------------------------
# Authority (stand-in for the CAPPO issuer: a key held outside the supervisor)
# ---------------------------------------------------------------------------

class Issuer:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()

    def issue(self, scenario: str, role: str, module: str, limits: dict,
              ttl_s: int = 300) -> dict:
        now = int(time.time())
        exec_id = str(uuid.uuid4())
        return contract.sign({
            "kind": "veklom.vre0.envelope/v1",
            "envelope_id": str(uuid.uuid4()),
            "execution_id": exec_id,
            "scenario": scenario,
            "role": role,
            "issued_at": now,
            "expires_at": now + ttl_s,
            "workload": {"module": module,
                         "module_sha256": sha256_file(WORKLOADS / f"{module}.wat"),
                         "entry": "run"},
            "resources": dict(limits),
            "evidence_level": "E2",
        }, self.key)


# ---------------------------------------------------------------------------
# One execution
# ---------------------------------------------------------------------------

class Execution:
    def __init__(self, sup: "Supervisor", signed_env: dict, launch_module: str,
                 fault: str | None) -> None:
        self.sup, self.signed_env, self.fault = sup, signed_env, fault
        self.env = signed_env["payload"]
        self.exec_id = self.env["execution_id"]
        self.launch_path = WORKLOADS / f"{launch_module}.wat"
        self.leaf = None
        self.proc: subprocess.Popen | None = None
        self.pidfd: int | None = None
        self.boundary: dict | None = None
        self.refusal: dict | None = None
        self.actions: list[dict] = []
        self.released = False
        self.released_at = self.deadline_at = self.reaped_at = None
        self.counters_before = self.counters_after = None
        self.wait_status = {"exit_code": None, "signal": None}
        self.report_raw = b""

    def refuse(self, stage: str, reasons: list[str]) -> None:
        self.refusal = {"stage": stage, "reasons": reasons, "at_unix_s": int(time.time())}

    # 1. authorize ------------------------------------------------------------
    def authorize(self) -> bool:
        reasons = []
        if not contract.verify(self.signed_env, self.sup.trusted_issuers):
            reasons.append("signature_not_trusted")
        if int(time.time()) >= self.env["expires_at"]:
            reasons.append("authority_expired")
        if reasons:
            self.refuse("authority", reasons)
            return False
        return True

    # 2-3. materialize and bind ----------------------------------------------
    def materialize(self) -> bool:
        res = self.env["resources"]
        self.leaf = self.sup.substrate.leaf(self.exec_id)
        written = res["memory_max_bytes"] * (8 if self.fault == "widen_memory" else 1)
        self.leaf.create(written)

        ready_r, ready_w = os.pipe()
        gate_r, gate_w = os.pipe()
        self.report_r, report_w = os.pipe()
        place = None if self.fault == "skip_placement" else self.leaf.place_self
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(HERE / "worker.py"), str(self.launch_path),
             str(res["compute_fuel"]), str(res["output_max_bytes"]),
             str(ready_w), str(gate_r), str(report_w)],
            pass_fds=(ready_w, gate_r, report_w), preexec_fn=place,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for fd in (ready_w, gate_r, report_w):
            os.close(fd)
        self.gate_w = gate_w
        self.pidfd = os.pidfd_open(self.proc.pid)
        identity = proc_identity(self.proc.pid)

        ready = b""
        deadline = time.monotonic() + READY_TIMEOUT_S
        while not ready.endswith(b"\n") and time.monotonic() < deadline:
            r, _, _ = select.select([ready_r], [], [], max(0.0, deadline - time.monotonic()))
            if not r:
                break
            chunk = os.read(ready_r, 4096)
            if not chunk:
                break
            ready += chunk
        os.close(ready_r)
        if not ready.endswith(b"\n"):
            self.refuse("binding", ["runtime_not_ready"])
            self.kill("binding_refused")
            return False
        runtime = json.loads(ready)

        readback = self.leaf.readback()
        membership = self.leaf.membership(self.proc.pid)
        sub_v = self.sup.substrate.version
        checks = {
            "memory_not_widened": readback["memory_limit_bytes"] <= res["memory_max_bytes"],
            "swap_not_widened": self._swap_bounded(readback, res["memory_max_bytes"]),
            "oom_kill_enabled": readback.get("oom_kill_disable", 0) == 0,
            "process_placed": all(m["match"] for m in membership.values()),
            "process_is_spawned_child": runtime["pid"] == self.proc.pid,
            "module_matches_envelope": runtime["module_sha256"] == self.env["workload"]["module_sha256"],
            "fuel_not_widened": runtime["fuel_set"] <= res["compute_fuel"],
            "output_not_widened": runtime["output_max"] <= res["output_max_bytes"],
        }
        self.boundary = {
            "kind": "veklom.vre0.boundary/v1",
            "execution_id": self.exec_id,
            "envelope_digest": contract.digest(self.env),
            "substrate": {"cgroup_version": sub_v, **self.sup.host_identity},
            "cgroup": self.leaf.identity(),
            "readback": readback,
            "process": {**identity, "membership": membership},
            "runtime": runtime,
            "wall_deadline_ms_enforced": res["wall_deadline_ms"],
            "checks": checks,
        }
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            self.refuse("binding", failed)
            self.kill("binding_refused")
            return False
        return True

    def _swap_bounded(self, readback: dict, memory_max: int) -> bool:
        if self.sup.substrate.version == 1:
            memsw = readback["memory_plus_swap_limit_bytes"]
            return memsw is None or memsw <= memory_max
        return readback["swap_limit_bytes"] in (None, 0)

    # 4. release --------------------------------------------------------------
    def release(self) -> None:
        self.counters_before = self.leaf.counters()
        self.released_wall_s = int(time.time())
        self.released_at = now_ns()
        self.deadline_at = self.released_at + self.env["resources"]["wall_deadline_ms"] * 1_000_000
        os.write(self.gate_w, b"G")
        os.close(self.gate_w)
        self.gate_w = None
        self.released = True

    # 5. enforce --------------------------------------------------------------
    def kill(self, reason: str) -> None:
        try:
            signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
            result = "delivered"
        except ProcessLookupError:
            result = "ESRCH"
        self.actions.append({"action": "SIGKILL", "reason": reason,
                             "at_ns": now_ns(), "result": result})

    def reap(self) -> None:
        rc = self.proc.wait()
        self.reaped_at = now_ns()
        self.wait_status = {"exit_code": rc if rc >= 0 else None,
                            "signal": -rc if rc < 0 else None}
        os.close(self.pidfd)
        if getattr(self, "gate_w", None) is not None:
            os.close(self.gate_w)
        os.set_blocking(self.report_r, False)
        try:
            while chunk := os.read(self.report_r, 65536):
                self.report_raw += chunk
        except BlockingIOError:
            pass
        os.close(self.report_r)
        self.guest_stdout = self.proc.stdout.read()
        self.proc.stdout.close()
        self.stderr_tail = self.proc.stderr.read()[-400:].decode("utf-8", "replace")
        self.proc.stderr.close()

    # 6-7. observe and receipt -------------------------------------------------
    def finish(self) -> dict:
        residual, teardown = [], None
        if self.leaf is not None and self.leaf.dirs and all(d.exists() for d in self.leaf.dirs.values()):
            if self.released:
                self.counters_after = self.leaf.counters()
            residual = self.leaf.procs()
            teardown = self.leaf.destroy()
        report = json.loads(self.report_raw) if self.report_raw else None
        evidence = {
            "kind": "veklom.vre0.evidence/v1",
            "execution_id": self.exec_id,
            "boundary_digest": contract.digest(self.boundary) if self.boundary else None,
            "refusal": self.refusal,
            "released": self.released,
            "released_wall_s": getattr(self, "released_wall_s", None),
            "released_at_ns": self.released_at,
            "deadline_at_ns": self.deadline_at,
            "reaped_at_ns": self.reaped_at,
            "counters_before": self.counters_before,
            "counters_after": self.counters_after,
            "supervisor_actions": self.actions,
            "wait_status": self.wait_status,
            "runtime_report": report,
            # Recorded so the claim is visible next to the outcome. classify()
            # never reads it.
            "untrusted_guest_output": getattr(self, "guest_stdout", b"")[:256].decode("utf-8", "replace"),
            "untrusted_guest_output_bytes": len(getattr(self, "guest_stdout", b"")),
            "residual_procs": residual,
            "teardown": teardown,
            "shim_stderr_tail": getattr(self, "stderr_tail", ""),
        }
        outcome = classify(evidence)
        receipt = contract.sign({
            "kind": "veklom.vre0.receipt/v1",
            "execution_id": self.exec_id,
            "envelope_digest": contract.digest(self.env),
            "boundary_digest": evidence["boundary_digest"],
            "evidence_digest": contract.digest(evidence),
            "outcome": outcome,
            "evidence_level": "E2",
        }, self.sup.executor_key)
        return {"envelope": self.signed_env, "boundary": self.boundary,
                "evidence": evidence, "receipt": receipt}


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class Supervisor:
    def __init__(self, run_id: str, issuer: Issuer) -> None:
        self.substrate = Substrate.detect(run_id)
        self.executor_key = Ed25519PrivateKey.generate()
        pub = contract.public_key_b64(issuer.key)
        self.trusted_issuers = {contract.key_id(pub): pub}
        facts = host_facts()
        self.host_identity = {"kernel": facts["kernel"], "boot_id": facts["boot_id"]}

    def run_pair(self, attacker: Execution, sibling: Execution) -> tuple[dict, dict]:
        live = []
        for ex in (sibling, attacker):          # sibling first: it is running when the attack starts
            if ex.authorize() and ex.materialize():
                live.append(ex)
        for ex in live:
            ex.release()
        pending = {ex.pidfd: ex for ex in live}
        while pending:
            next_deadline = min(ex.deadline_at for ex in pending.values())
            timeout_ms = max(0, (next_deadline - now_ns()) // 1_000_000)
            poller = select.poll()
            for fd in pending:
                poller.register(fd, select.POLLIN)
            ready = {fd for fd, _ in poller.poll(timeout_ms)}
            for fd, ex in list(pending.items()):
                if fd in ready:
                    ex.reap()
                    del pending[fd]
                elif now_ns() >= ex.deadline_at and not ex.actions:
                    ex.kill("wall_deadline")
        for ex in (attacker, sibling):
            if ex.proc is not None and ex.reaped_at is None:
                ex.reap()                       # refused executions: already SIGKILLed
        return attacker.finish(), sibling.finish()


def host_canary() -> dict:
    t = now_ns()
    rc = subprocess.run(["/bin/true"]).returncode
    return {"exit_code": rc, "latency_us": (now_ns() - t) // 1000}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=10)
    ap.add_argument("--scenarios", nargs="*", default=list(SCENARIOS))
    ap.add_argument("--out", type=pathlib.Path, default=HERE / "runs")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("harness needs root to create cgroups", file=sys.stderr)
        return 2

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / run_id
    (out / "executions").mkdir(parents=True)
    issuer = Issuer()
    sup = Supervisor(run_id, issuer)
    untrusted_issuer = Issuer()                 # signs the forged_authority envelopes

    from importlib.metadata import version
    manifest = {
        "kind": "veklom.vre0.run/v1",
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "host": host_facts(),
        "substrate": sup.substrate.describe(),
        "python": sys.version.split()[0],
        "wasmtime_py": version("wasmtime"),
        "cryptography": version("cryptography"),
        "code_sha256": {p.name: sha256_file(p) for p in sorted(HERE.glob("*.py"))},
        "workload_sha256": {p.name: sha256_file(p) for p in sorted(WORKLOADS.glob("*.wat"))},
        "limits": {"attacker": ATTACK_LIMITS, "attacker_overrides": LIMIT_OVERRIDES,
                   "sibling": SIBLING_LIMITS},
        "trusted_keys": {
            "issuer": {contract.key_id(contract.public_key_b64(issuer.key)):
                       contract.public_key_b64(issuer.key)},
            "executor": {contract.key_id(contract.public_key_b64(sup.executor_key)):
                         contract.public_key_b64(sup.executor_key)},
        },
        "expected": {s: SCENARIOS[s][3] for s in args.scenarios},
        "iterations": args.iterations,
        "pairs": [],
    }

    # Calibration: one sibling alone fixes the value every sibling must return.
    calib = Execution(sup, issuer.issue("calibration", "sibling", "sibling", SIBLING_LIMITS),
                      "sibling", None)
    if calib.authorize() and calib.materialize():
        calib.release()
        while not select.select([calib.pidfd], [], [], 10)[0]:
            calib.kill("wall_deadline")
        calib.reap()
    calib_rec = calib.finish()
    (out / "executions" / "calibration.json").write_text(json.dumps(calib_rec, indent=1))
    rep = calib_rec["evidence"]["runtime_report"]
    if calib_rec["receipt"]["payload"]["outcome"] != "COMPLETED" or rep is None:
        print("calibration sibling did not complete; refusing to run", file=sys.stderr)
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        return 1
    manifest["sibling_expected_return_value"] = rep["return_value"]

    try:
        for it in range(args.iterations):
            for scen in args.scenarios:
                env_module, launch_module, fault, expected = SCENARIOS[scen]
                signer = untrusted_issuer if fault == "forged" else issuer
                limits = {**ATTACK_LIMITS, **LIMIT_OVERRIDES.get(scen, {})}
                a_env = signer.issue(scen, "attacker", env_module, limits,
                                     ttl_s=-1 if fault == "expired" else 300)
                s_env = issuer.issue(scen, "sibling", "sibling", SIBLING_LIMITS)
                attacker = Execution(sup, a_env, launch_module,
                                     fault if fault not in ("expired", "forged") else None)
                sibling = Execution(sup, s_env, "sibling", None)
                host_before = {"mem_available_kib": mem_available_kib(),
                               "supervisor_oom_kill": sup.substrate.supervisor_oom_kill()}
                a_rec, s_rec = sup.run_pair(attacker, sibling)
                host_after = {"mem_available_kib": mem_available_kib(),
                              "supervisor_oom_kill": sup.substrate.supervisor_oom_kill(),
                              "canary": host_canary()}
                name = f"{it:02d}-{scen}"
                for role, rec in (("attacker", a_rec), ("sibling", s_rec)):
                    (out / "executions" / f"{name}-{role}.json").write_text(json.dumps(rec, indent=1))
                outcome = a_rec["receipt"]["payload"]["outcome"]
                manifest["pairs"].append({
                    "iteration": it, "scenario": scen,
                    "attacker": f"executions/{name}-attacker.json",
                    "sibling": f"executions/{name}-sibling.json",
                    "host_before": host_before, "host_after": host_after,
                })
                mark = "ok " if outcome == expected else "BAD"
                print(f"[{mark}] {name:<26} {outcome:<28} sibling="
                      f"{s_rec['receipt']['payload']['outcome']}", flush=True)
    finally:
        sup.substrate.teardown()
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"run written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
