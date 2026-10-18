#!/usr/bin/env python3
"""Independent verifier for a VRE-0 run directory.

Reads stored artifacts only; runs no workload and needs no root. Imports
nothing from the harness: canonical encoding, signature checks, the binding
checks and the outcome rule are re-implemented here from the written rules, so
a harness bug cannot vouch for itself.

    python3 -m vre0.gate.verify vre0/gate/runs/<run_id> [--trust trust.json]

Without --trust, trusted keys are taken from the run manifest, which is
self-asserted (the verifier says so). Pin keys for a real check.

What this proves and what it cannot (evidence level E2):
  - every receipt is signed by the pinned executor key and commits to the
    exact envelope, boundary and evidence bytes stored beside it;
  - every envelope is signed by the pinned issuer key, or the execution was
    refused for that reason;
  - the boundary readback is no wider than the envelope, and the process that
    ran was a member of that boundary's cgroup;
  - the outcome follows from substrate evidence alone (never guest output);
  - a sibling in its own cgroup was running for each attacker's whole life
    and still completed with the value fixed by a solo calibration run.
  It cannot catch a supervisor that lies consistently across all records:
  the supervisor is inside the trust domain at E2. That needs an independent
  observer (E3).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SIGKILL = 9
# Poll/reap slack allowed between the deadline and the supervisor noticing a
# process that exited without being killed.
REAP_SLACK_NS = 25_000_000
EXECUTION_OUTCOMES = {"COMPLETED", "ENVELOPE_MEMORY_EXCEEDED", "ENVELOPE_COMPUTE_EXHAUSTED",
                      "ENVELOPE_DEADLINE_EXCEEDED", "ENVELOPE_OUTPUT_EXCEEDED"}


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def sig_ok(signed: dict, trusted: dict) -> bool:
    pub = trusted.get(signed.get("key_id"))
    if pub is None or signed.get("alg") != "Ed25519":
        return False
    raw = base64.b64decode(pub)
    if hashlib.sha256(raw).hexdigest()[:16] != signed["key_id"]:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(
            base64.b64decode(signed["sig"]), canonical(signed["payload"]))
        return True
    except Exception:
        return False


def outcome_from_evidence(ev: dict) -> str:
    """The outcome rule, from substrate evidence only."""
    if ev["refusal"] is not None:
        if ev["released"]:
            return "INDETERMINATE"
        return {"authority": "AUTHORITY_REFUSED",
                "binding": "BINDING_REFUSED"}.get(ev["refusal"]["stage"], "INDETERMINATE")
    if not ev["released"]:
        return "INDETERMINATE"
    oom = ev["counters_after"]["oom_kill"] - ev["counters_before"]["oom_kill"]
    killed = any(a["action"] == "SIGKILL" and a["reason"] == "wall_deadline"
                 and a["result"] == "delivered" for a in ev["supervisor_actions"])
    sig = ev["wait_status"]["signal"]
    code = ev["wait_status"]["exit_code"]
    rep = ev["runtime_report"]
    if oom >= 1 and sig == SIGKILL and not killed and rep is None:
        return "ENVELOPE_MEMORY_EXCEEDED"
    if killed and sig == SIGKILL and oom == 0 and rep is None:
        return "ENVELOPE_DEADLINE_EXCEEDED"
    if oom == 0 and not killed and sig is None and rep is not None:
        if code == 3 and rep.get("trap") == "fuel" and rep.get("fuel_remaining") == 0:
            return "ENVELOPE_COMPUTE_EXHAUSTED"
        if code == 5 and rep.get("trap") == "output_limit":
            return "ENVELOPE_OUTPUT_EXCEEDED"
        if code == 0 and rep.get("status") == "returned":
            return "COMPLETED"
    return "INDETERMINATE"


def binding_checks(env: dict, b: dict) -> dict:
    """Recompute the non-widening and placement checks from raw fields."""
    res, rb, rt = env["resources"], b["readback"], b["runtime"]
    if b["substrate"]["cgroup_version"] == 1:
        memsw = rb.get("memory_plus_swap_limit_bytes")
        swap_ok = memsw is None or memsw <= res["memory_max_bytes"]
    else:
        swap_ok = rb.get("swap_limit_bytes") in (None, 0)
    rel = b["cgroup"]["relative_paths"]
    mem = b["process"]["membership"]
    return {
        "memory_not_widened": rb["memory_limit_bytes"] <= res["memory_max_bytes"],
        "swap_not_widened": swap_ok,
        "oom_kill_enabled": rb.get("oom_kill_disable", 0) == 0,
        "process_placed": set(mem) == set(rel) and all(
            mem[h]["observed"] == rel[h] for h in rel),
        "process_is_spawned_child": rt["pid"] == b["process"]["pid"],
        "module_matches_envelope": rt["module_sha256"] == env["workload"]["module_sha256"],
        "fuel_not_widened": rt["fuel_set"] <= res["compute_fuel"],
        "output_not_widened": rt["output_max"] <= res["output_max_bytes"],
    }


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.count = 0

    def __call__(self, ok: bool, where: str, what: str) -> bool:
        self.count += 1
        if not ok:
            self.failures.append(f"{where}: {what}")
        return ok


def verify_execution(rec: dict, trust: dict, check: Checker, where: str) -> dict:
    """Checks one execution record. Returns derived facts for pair checks."""
    env_signed, b, ev, rcpt = rec["envelope"], rec["boundary"], rec["evidence"], rec["receipt"]
    env = env_signed["payload"]
    r = rcpt["payload"]
    env_d = digest(env)
    issuer_ok = sig_ok(env_signed, trust["issuer"])

    check(sig_ok(rcpt, trust["executor"]), where, "receipt signature not valid under a pinned executor key")
    check(r["envelope_digest"] == env_d, where, "receipt does not commit to this envelope")
    check(r["execution_id"] == env["execution_id"] == ev["execution_id"], where, "execution_id differs across records")
    check(r["evidence_digest"] == digest(ev), where, "receipt does not commit to this evidence")
    b_d = digest(b) if b is not None else None
    check(r["boundary_digest"] == b_d and ev["boundary_digest"] == b_d, where,
          "receipt/evidence do not commit to this boundary")
    outcome = outcome_from_evidence(ev)
    check(outcome == r["outcome"], where,
          f"outcome {r['outcome']} does not follow from evidence (evidence gives {outcome})")

    if b is not None:
        check(b["envelope_digest"] == env_d, where, "boundary was materialized for a different envelope")
        check(b["execution_id"] == env["execution_id"], where, "boundary belongs to a different execution")
        check(all(pathlib.PurePosixPath(p).name == env["execution_id"]
                  for p in b["cgroup"]["relative_paths"].values()), where,
              "cgroup is not named for this execution")
        recomputed = binding_checks(env, b)
        check(recomputed == b["checks"], where,
              f"boundary checks as recorded {b['checks']} differ from recomputed {recomputed}")
        b_failed = sorted(k for k, ok in recomputed.items() if not ok)
    else:
        b_failed = None

    if r["outcome"] in EXECUTION_OUTCOMES:
        check(issuer_ok, where, "execution released on an envelope not signed by a pinned issuer")
        check(b is not None and b_failed == [], where, f"execution released on a failing boundary: {b_failed}")
        check(ev["released_wall_s"] is not None and
              env["issued_at"] <= ev["released_wall_s"] < env["expires_at"], where,
              "execution released outside the envelope's validity window")
        check(ev["residual_procs"] == [], where, "processes left in the boundary after reaping")
        check(ev["teardown"] is not None and all(v == "removed" for v in ev["teardown"].values()),
              where, "boundary not torn down")
        kills = [a for a in ev["supervisor_actions"] if a["reason"] == "wall_deadline"]
        if kills:
            check(all(a["at_ns"] >= ev["deadline_at_ns"] for a in kills), where,
                  "deadline kill sent before the deadline")
        else:
            check(ev["reaped_at_ns"] <= ev["deadline_at_ns"] + REAP_SLACK_NS, where,
                  "ran past the wall deadline without being killed")
        check(ev["deadline_at_ns"] - ev["released_at_ns"] ==
              env["resources"]["wall_deadline_ms"] * 1_000_000, where,
              "enforced deadline differs from the envelope deadline")
    elif r["outcome"] == "AUTHORITY_REFUSED":
        reasons = set(ev["refusal"]["reasons"])
        expect = set()
        if not issuer_ok:
            expect.add("signature_not_trusted")
        if ev["refusal"]["at_unix_s"] >= env["expires_at"]:
            expect.add("authority_expired")
        check(bool(expect) and reasons == expect, where,
              f"authority refusal reasons {sorted(reasons)} not borne out (verifier finds {sorted(expect)})")
        check(b is None and ev["wait_status"]["exit_code"] is None and ev["runtime_report"] is None,
              where, "something was materialized or ran despite an authority refusal")
    elif r["outcome"] == "BINDING_REFUSED":
        check(not ev["released"] and ev["runtime_report"] is None, where,
              "guest ran despite a binding refusal")
        if b is not None:
            check(b_failed != [] and sorted(ev["refusal"]["reasons"]) == b_failed, where,
                  f"binding refusal reasons {ev['refusal']['reasons']} not borne out (verifier finds {b_failed})")
        else:
            check(ev["refusal"]["reasons"] == ["runtime_not_ready"], where,
                  "binding refused without a boundary record")
    return {"outcome": r["outcome"], "env": env, "boundary": b, "evidence": ev}


def oom_delta(ev: dict) -> int | None:
    if not ev["released"]:
        return None
    return ev["counters_after"]["oom_kill"] - ev["counters_before"]["oom_kill"]


def verify_run(run: dict, trust: dict) -> dict:
    """run = {"manifest": ..., "records": {relative_path: record}}"""
    m = run["manifest"]
    check = Checker()
    stats: dict[str, dict] = {}

    calib = verify_execution(run["records"]["executions/calibration.json"], trust, check, "calibration")
    check(calib["outcome"] == "COMPLETED" and
          calib["evidence"]["runtime_report"]["return_value"] == m["sibling_expected_return_value"],
          "calibration", "calibration sibling did not fix the expected sibling value")
    boot_id = m["host"]["boot_id"]

    for pair in m["pairs"]:
        where = f"{pair['iteration']:02d}-{pair['scenario']}"
        a = verify_execution(run["records"][pair["attacker"]], trust, check, where + "/attacker")
        s = verify_execution(run["records"][pair["sibling"]], trust, check, where + "/sibling")
        check(a["env"]["scenario"] == s["env"]["scenario"] == pair["scenario"]
              and a["env"]["role"] == "attacker" and s["env"]["role"] == "sibling",
              where, "records filed under the wrong scenario or role")
        check(a["outcome"] == m["expected"][pair["scenario"]], where,
              f"attacker outcome {a['outcome']} != expected {m['expected'][pair['scenario']]}")
        for x in (a, s):
            if x["boundary"] is not None:
                check(x["boundary"]["substrate"]["boot_id"] == boot_id, where, "boundary from another boot")

        # Containment: the sibling ran at the same time, unharmed.
        sev = s["evidence"]
        check(s["outcome"] == "COMPLETED" and
              sev["runtime_report"]["return_value"] == m["sibling_expected_return_value"],
              where, "sibling did not complete with the expected value")
        check(oom_delta(sev) == 0, where, "sibling saw an OOM kill")
        if a["boundary"] is not None and s["boundary"] is not None:
            check(a["boundary"]["cgroup"]["relative_paths"] != s["boundary"]["cgroup"]["relative_paths"],
                  where, "attacker and sibling share a cgroup")
        aev = a["evidence"]
        if aev["released"]:
            # The sibling must be live for the attacker's whole life, so it is
            # running at the moment the boundary is enforced.
            check(sev["released_at_ns"] <= aev["released_at_ns"] and aev["reaped_at_ns"] < sev["reaped_at_ns"],
                  where, "sibling was not running for the attacker's whole lifetime")
        hb, ha = pair["host_before"], pair["host_after"]
        check(ha["canary"]["exit_code"] == 0, where, "host canary failed after the scenario")
        check(ha["supervisor_oom_kill"] == hb["supervisor_oom_kill"], where, "supervisor's cgroup saw an OOM kill")

        st = stats.setdefault(pair["scenario"], {"outcomes": {}, "elapsed_ms": [], "attacker_peak_bytes": [],
                                                 "sibling_elapsed_ms": [], "deadline_kill_to_reap_ms": []})
        st["outcomes"][a["outcome"]] = st["outcomes"].get(a["outcome"], 0) + 1
        if aev["released"]:
            st["elapsed_ms"].append((aev["reaped_at_ns"] - aev["released_at_ns"]) / 1e6)
            st["attacker_peak_bytes"].append(aev["counters_after"]["peak_bytes"])
            for k in aev["supervisor_actions"]:
                if k["reason"] == "wall_deadline":
                    st["deadline_kill_to_reap_ms"].append((aev["reaped_at_ns"] - k["at_ns"]) / 1e6)
        st["sibling_elapsed_ms"].append((sev["reaped_at_ns"] - sev["released_at_ns"]) / 1e6)

    return {"run_id": m["run_id"], "pairs": len(m["pairs"]), "checks": check.count,
            "failures": check.failures, "valid": not check.failures, "stats": summarize(stats)}


def summarize(stats: dict) -> dict:
    def q(xs, p):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) if xs else None
    out = {}
    for scen, st in stats.items():
        out[scen] = {"outcomes": st["outcomes"]}
        for k in ("elapsed_ms", "sibling_elapsed_ms", "deadline_kill_to_reap_ms"):
            if st[k]:
                out[scen][k] = {"p50": q(st[k], .5), "p95": q(st[k], .95), "max": round(max(st[k]), 2)}
        if st["attacker_peak_bytes"]:
            out[scen]["attacker_peak_MiB_max"] = round(max(st["attacker_peak_bytes"]) / 2**20, 2)
    return out


def load_run(d: pathlib.Path) -> dict:
    manifest = json.loads((d / "manifest.json").read_text())
    records = {str(p.relative_to(d)): json.loads(p.read_text())
               for p in sorted((d / "executions").glob("*.json"))}
    return {"manifest": manifest, "records": records}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=pathlib.Path)
    ap.add_argument("--trust", type=pathlib.Path,
                    help='JSON {"issuer": {key_id: pub_b64}, "executor": {key_id: pub_b64}}')
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    run = load_run(args.run_dir)
    if args.trust:
        trust = json.loads(args.trust.read_text())
    else:
        trust = run["manifest"]["trusted_keys"]
        print("note: trust taken from the run manifest (self-asserted); pin keys with --trust",
              file=sys.stderr)
    report = verify_run(run, trust)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"run {report['run_id']}: {report['pairs']} pairs, {report['checks']} checks, "
              f"{'VALID' if report['valid'] else 'INVALID'}")
        for f in report["failures"]:
            print("  FAIL", f)
        for scen, st in report["stats"].items():
            print(f"  {scen:<20} {st}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
