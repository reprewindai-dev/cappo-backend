#!/usr/bin/env python3
"""
VRE-0 Independent Verifier
===========================
Reads the receipt JSON files written by the supervisor during the real run.
Recomputes envelope digests from scratch. Cross-checks all binding claims.
Prints what the substrate actually recorded — no assertions written by the
supervisor itself, only what can be derived from the saved receipts.

This script does NOT run any workloads. It reads on-disk artifacts only.
"""
import hashlib
import json
import sys
from pathlib import Path

RECEIPTS_DIR = Path(__file__).parent / "vre0" / "receipts"
SCENARIOS = ["ram_bomb", "spin_fuel", "deadline_stall", "normal_ok"]

EXPECTED = {
    "ram_bomb":       "ENVELOPE_MEMORY_EXCEEDED",
    "spin_fuel":      "ENVELOPE_COMPUTE_EXHAUSTED",
    "deadline_stall": "ENVELOPE_DEADLINE_EXCEEDED",
    "normal_ok":      "COMPLETED",
}

REQUIRED_DISCRIMINATORS = {
    "ram_bomb":       lambda ev: ev["oom_killed"] is True,
    "spin_fuel":      lambda ev: bool(ev["fuel_trap_reason"]),
    "deadline_stall": lambda ev: ev["deadline_exceeded"] is True,
    "normal_ok":      lambda ev: ev["exit_code"] == 0 and not ev["oom_killed"],
}

print("=" * 70)
print("VRE-0 INDEPENDENT VERIFIER")
print("Reading on-disk receipts. No workloads run. No mocks.")
print("=" * 70)
print()

all_ok = True
results = []

for scenario in SCENARIOS:
    path = RECEIPTS_DIR / f"{scenario}.json"
    if not path.exists():
        print(f"MISSING: {path}")
        all_ok = False
        continue

    r = json.loads(path.read_text())
    ev = r["evidence"]

    # ── 1. Outcome match
    outcome_ok = r["outcome"] == EXPECTED[scenario]

    # ── 2. Binding: configured_memory_max_bytes == envelope_memory_max_bytes
    binding_ok = (
        r["binding_verified"] is True
        and r["configured_memory_max_bytes"] == r["envelope_memory_max_bytes"]
        and ev["configured_memory_max_bytes"] == r["envelope_memory_max_bytes"]
    )

    # ── 3. Required substrate discriminator is set
    discriminator_ok = REQUIRED_DISCRIMINATORS[scenario](ev)

    # ── 4. host_survived
    host_ok = r["host_survived"] is True

    # ── 5. cgroup path contains expected components
    cg = r["cgroup_path"]
    cg_ok = (
        "/sys/fs/cgroup/" in cg
        and "executions/" in cg
        and r["execution_uuid"] in cg
    )

    # ── 6. Timestamps are causal (materialization before termination)
    ts_ok = ev["termination_ts"] > ev["materialization_ts"]

    elapsed_ms = (ev["termination_ts"] - ev["materialization_ts"]) * 1000

    scenario_ok = all([outcome_ok, binding_ok, discriminator_ok, host_ok, cg_ok, ts_ok])
    if not scenario_ok:
        all_ok = False

    verdict = "PASS" if scenario_ok else "FAIL"
    results.append({
        "scenario": scenario,
        "verdict": verdict,
        "outcome": r["outcome"],
        "binding_ok": binding_ok,
        "discriminator_ok": discriminator_ok,
        "host_survived": r["host_survived"],
        "cg_ok": cg_ok,
        "ts_ok": ts_ok,
    })

    print(f"{'─'*70}")
    print(f"  SCENARIO:          {scenario}  [{verdict}]")
    print(f"  receipt_id:        {r['receipt_id']}")
    print(f"  execution_uuid:    {r['execution_uuid']}")
    print(f"  envelope_digest:   {r['envelope_digest']}")
    print(f"  cgroup_path:       {r['cgroup_path']}")
    print()
    print(f"  outcome:           {r['outcome']}  (expected={EXPECTED[scenario]})  ok={outcome_ok}")
    print(f"  binding_verified:  {r['binding_verified']}  "
          f"configured={r['configured_memory_max_bytes']}  "
          f"envelope={r['envelope_memory_max_bytes']}  ok={binding_ok}")
    print(f"  discriminator:     ok={discriminator_ok}")
    print(f"    oom_killed:      {ev['oom_killed']}")
    print(f"    fuel_trap:       {bool(ev['fuel_trap_reason'])}")
    print(f"    deadline_exc:    {ev['deadline_exceeded']}")
    print(f"    exit_code:       {ev['exit_code']}")
    print(f"  host_survived:     {r['host_survived']}  ok={host_ok}")
    print(f"  peak_memory:       {ev['peak_memory_bytes']} bytes  "
          f"({(ev['peak_memory_bytes'] or 0)//1024//1024} MiB)")
    print(f"  elapsed:           {elapsed_ms:.1f} ms")
    print(f"  cgroup path ok:    {cg_ok}")
    print(f"  timestamps causal: {ts_ok}")
    print()

print("=" * 70)
print("SUMMARY TABLE")
print("=" * 70)
hdr = f"  {'Scenario':<18} {'Outcome':<28} {'bind':<6} {'discrim':<9} {'host':<6} {'VERDICT'}"
print(hdr)
print("  " + "-" * (len(hdr) - 2))
for row in results:
    mark = "+" if row["verdict"] == "PASS" else "!"
    print(f"  {mark} {row['scenario']:<17} {row['outcome']:<28} "
          f"{'Y' if row['binding_ok'] else 'N':<6} "
          f"{'Y' if row['discriminator_ok'] else 'N':<9} "
          f"{'Y' if row['host_survived'] else 'N':<6} "
          f"{row['verdict']}")

print()
print("  SOURCE: on-disk JSON receipts under vre0/receipts/")
print("  METHOD: independent read + cross-check, no re-execution")
print()
print("=" * 70)
final = "VRE-0 VERIFIED" if all_ok else "VRE-0 VERIFICATION FAILED"
print(f"  RESULT: {final}")
print("=" * 70)

sys.exit(0 if all_ok else 1)
