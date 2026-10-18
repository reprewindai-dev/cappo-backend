#!/usr/bin/env python3
"""Tamper suite: shows the verifier rejects forged or altered VRE-0 artifacts.

    python3 -m vre0.gate.tamper vre0/gate/runs/<run_id>

Each case copies the stored run in memory, alters it, and runs the verifier.
A case passes only if the verifier rejects the altered run and names the
expected failure.

Two kinds of attacker:
  outsider     cannot sign; edits records and keeps the original signatures.
  compromised  holds an executor key the verifier trusts (simulated with a
               fresh key added to the trust set) and re-signs after editing,
               so only the verifier's own logic can catch the lie.

The last case is a known limit, not a pass: a compromised supervisor that
rewrites every record consistently (and the run's stated expectation)
cannot be caught at E2. It is reported as accepted.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vre0.gate import contract
from vre0.gate.verify import digest, load_run, verify_run

MiB = 1024 * 1024


class Case:
    def __init__(self, name, attacker, expect, mutate):
        self.name, self.attacker, self.expect, self.mutate = name, attacker, expect, mutate


def find(run, scenario, role, iteration=0):
    for p in run["manifest"]["pairs"]:
        if p["scenario"] == scenario and p["iteration"] == iteration:
            return p[role]
    raise KeyError(scenario)


def rec(run, scenario, role="attacker"):
    return run["records"][find(run, scenario, role)]


def reseal(r, key):
    """Recompute digests and re-sign the receipt, as a compromised executor would."""
    p = dict(r["receipt"]["payload"])
    if r["boundary"] is not None:
        r["evidence"]["boundary_digest"] = digest(r["boundary"])
    p["boundary_digest"] = digest(r["boundary"]) if r["boundary"] is not None else None
    p["evidence_digest"] = digest(r["evidence"])
    p["envelope_digest"] = digest(r["envelope"]["payload"])
    r["receipt"] = contract.sign(p, key)


def cases(evil_exec: Ed25519PrivateKey, evil_issuer: Ed25519PrivateKey):
    def widen_envelope(run):
        rec(run, "ram_bomb")["envelope"]["payload"]["resources"]["memory_max_bytes"] = 512 * MiB

    def widen_envelope_resigned(run):
        r = rec(run, "ram_bomb")
        r["envelope"]["payload"]["resources"]["memory_max_bytes"] = 512 * MiB
        r["envelope"] = contract.sign(r["envelope"]["payload"], evil_issuer)

    def flip_outcome(run):
        rec(run, "ram_bomb")["receipt"]["payload"]["outcome"] = "COMPLETED"

    def resign_unpinned(run):
        r = rec(run, "ram_bomb")
        r["receipt"] = contract.sign(r["receipt"]["payload"], evil_issuer)

    def transplant_boundary(run):
        r = rec(run, "ram_bomb")
        r["boundary"] = copy.deepcopy(rec(run, "ram_bomb", "sibling")["boundary"])
        reseal(r, evil_exec)

    def readback_512(run):
        r = rec(run, "normal_ok")
        r["boundary"]["readback"]["memory_limit_bytes"] = 512 * MiB
        r["boundary"]["readback"]["memory_plus_swap_limit_bytes"] = 512 * MiB
        reseal(r, evil_exec)                       # checks left claiming all-true

    def hide_oom(run):
        r = rec(run, "ram_bomb")
        r["evidence"]["counters_after"]["oom_kill"] = r["evidence"]["counters_before"]["oom_kill"]
        reseal(r, evil_exec)

    def believe_the_guest(run):
        r = rec(run, "lying_bomb")
        r["receipt"]["payload"]["outcome"] = "COMPLETED"   # what the guest printed
        reseal(r, evil_exec)

    def foreign_membership(run):
        r = rec(run, "normal_ok")
        sib = rec(run, "normal_ok", "sibling")["boundary"]["cgroup"]["relative_paths"]
        for h, m in r["boundary"]["process"]["membership"].items():
            m["observed"] = sib[h]
        reseal(r, evil_exec)

    def sibling_finished_early(run):
        a, s = rec(run, "ram_bomb"), rec(run, "ram_bomb", "sibling")
        s["evidence"]["reaped_at_ns"] = a["evidence"]["released_at_ns"] + 1
        reseal(s, evil_exec)

    def sibling_wrong_value(run):
        s = rec(run, "ram_bomb", "sibling")
        s["evidence"]["runtime_report"]["return_value"] = "0"
        reseal(s, evil_exec)

    def released_after_expiry(run):
        r = rec(run, "normal_ok")
        r["evidence"]["released_wall_s"] = r["envelope"]["payload"]["expires_at"] + 1
        reseal(r, evil_exec)

    def early_deadline_kill(run):
        r = rec(run, "deadline_stall")
        for a in r["evidence"]["supervisor_actions"]:
            a["at_ns"] = r["evidence"]["released_at_ns"] + 1_000_000
        reseal(r, evil_exec)

    def run_past_deadline(run):
        r = rec(run, "normal_ok")
        r["evidence"]["reaped_at_ns"] = r["evidence"]["deadline_at_ns"] + 500_000_000
        reseal(r, evil_exec)

    def refused_but_ran(run):
        r = rec(run, "widened_boundary")
        r["evidence"]["refusal"] = None
        r["receipt"]["payload"]["outcome"] = "INDETERMINATE"
        reseal(r, evil_exec)

    def forged_authority_accepted(run):
        r = rec(run, "forged_authority")
        r["evidence"]["refusal"]["reasons"] = ["authority_expired"]
        reseal(r, evil_exec)

    def consistent_lie(run):
        # Rewrite every ram_bomb as a clean completion, manifest included.
        run["manifest"]["expected"]["ram_bomb"] = "COMPLETED"
        for p in run["manifest"]["pairs"]:
            if p["scenario"] != "ram_bomb":
                continue
            r = run["records"][p["attacker"]]
            ev = r["evidence"]
            ev["counters_after"]["oom_kill"] = ev["counters_before"]["oom_kill"]
            ev["wait_status"] = {"exit_code": 0, "signal": None}
            ev["runtime_report"] = {"status": "returned", "return_value": "192",
                                    "fuel_remaining": 1, "fuel_consumed": 1, "output_bytes": 0}
            r["receipt"]["payload"]["outcome"] = "COMPLETED"
            reseal(r, evil_exec)

    return [
        Case("widened envelope, original signature", "outsider", "receipt does not commit to this envelope", widen_envelope),
        Case("widened envelope, re-signed by unpinned issuer", "outsider", "not signed by a pinned issuer", widen_envelope_resigned),
        Case("receipt outcome flipped", "outsider", "receipt signature not valid", flip_outcome),
        Case("receipt re-signed by unpinned key", "outsider", "receipt signature not valid", resign_unpinned),
        Case("boundary transplanted from sibling", "compromised", "boundary was materialized for a different envelope", transplant_boundary),
        Case("readback widened to 512 MiB, checks left true", "compromised", "differ from recomputed", readback_512),
        Case("OOM kill hidden, outcome kept", "compromised", "does not follow from evidence", hide_oom),
        Case("guest's COMPLETED claim adopted", "compromised", "does not follow from evidence", believe_the_guest),
        Case("process membership pointed at sibling cgroup", "compromised", "differ from recomputed", foreign_membership),
        Case("sibling finished before the attack", "compromised", "sibling was not running", sibling_finished_early),
        Case("sibling return value altered", "compromised", "sibling did not complete with the expected value", sibling_wrong_value),
        Case("released after authority expiry", "compromised", "outside the envelope's validity window", released_after_expiry),
        Case("deadline kill sent early", "compromised", "deadline kill sent before the deadline", early_deadline_kill),
        Case("ran past deadline unkilled", "compromised", "ran past the wall deadline", run_past_deadline),
        Case("refused execution relabelled", "compromised", "!= expected", refused_but_ran),
        Case("forged authority refusal re-reasoned", "compromised", "not borne out", forged_authority_accepted),
        Case("KNOWN LIMIT: consistent lie across all records", "compromised", None, consistent_lie),
    ]


def main() -> int:
    run_dir = pathlib.Path(sys.argv[1])
    base = load_run(run_dir)
    trust = base["manifest"]["trusted_keys"]
    if len(sys.argv) > 2:
        trust = json.loads(pathlib.Path(sys.argv[2]).read_text())
    evil_exec, evil_issuer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    evil_pub = contract.public_key_b64(evil_exec)
    compromised_trust = {"issuer": trust["issuer"],
                         "executor": {**trust["executor"], contract.key_id(evil_pub): evil_pub}}

    clean = verify_run(copy.deepcopy(base), trust)
    print(f"baseline: {'VALID' if clean['valid'] else 'INVALID'} ({clean['checks']} checks)")
    if not clean["valid"]:
        for f in clean["failures"]:
            print("  FAIL", f)
        return 1

    ok = True
    for c in cases(evil_exec, evil_issuer):
        run = copy.deepcopy(base)
        c.mutate(run)
        rep = verify_run(run, compromised_trust if c.attacker == "compromised" else trust)
        if c.expect is None:
            status = "ACCEPTED (known E2 limit)" if rep["valid"] else "rejected"
            print(f"  [limit] {c.name}: {status}")
            continue
        hit = (not rep["valid"]) and any(c.expect in f for f in rep["failures"])
        ok &= hit
        first = next((f for f in rep["failures"] if c.expect in f), rep["failures"][:1])
        print(f"  [{'pass' if hit else 'FAIL'}] {c.attacker:<11} {c.name}: "
              f"{'rejected: ' + str(first) if hit else 'NOT REJECTED as expected ' + repr(rep['failures'][:3])}")
    print("tamper suite:", "ALL REJECTED" if ok else "GAPS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
