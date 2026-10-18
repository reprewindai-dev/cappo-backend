#!/usr/bin/env python3
"""Pre-registered transfer test (cgroup v2): the verdict, from a manifest.

    python -m vre15.transfer vre15/runs/<run_id>/manifest.json

Prints one short JSON summary, small enough to paste. It holds the
pre-registered decisions (T1, T2; see vre15/README.md, "Pre-registered:
transfer to WSL2 / cgroup v2", as amended before any run) and the
reported-only measurements. Per-seed values and every exclusion are listed,
so the verdict can be recomputed from the summary alone.

Decision rules (Amendment 1):
- The test applies only to a manifest whose host reports cgroup v2.
- The run is complete only with exactly one row per arm (native,
  mapped-ckpt) for each seed 801-815, and no other rows.
- T1: confirmed only if the run is complete and mapped-ckpt has strictly
  fewer mean resident pages per revoked tenant, 5 s after authority ends,
  on at least 12 of the 15 seeds. A seed where either arm failed, was
  OOM-killed or has no measurement, or where the values tie, does not
  count as better. The sign test p over usable pairs is reported only.
- T2: confirmed only if the run is complete and every one of the 15
  mapped-ckpt runs exited 0 with no OOM kill, had 0 integrity failures and
  0 authority violations, and its evidence verified VALID W1 with 0 private
  pages held after every revocation. A missing or failed run fails T2.
"""

import hashlib
import json
import pathlib
import statistics as st
import sys
from collections import Counter

from vre15.paired import sign_test

SEEDS = tuple(range(801, 816))
ARMS = ("native", "mapped-ckpt")
MIN_BETTER = 12


def ok_row(r):
    return r.get("exit_code") == 0 and not r.get("oom_kills")


def residency(r, at="5.0"):
    return ((r.get("revoked_residency") or {}).get(at) or {}).get("mean_pages")


def t2_failures(r):
    """Reasons a mapped-ckpt run fails T2; empty means it passes."""
    if r is None:
        return ["missing"]
    out = []
    if r.get("exit_code") != 0:
        out.append(f"exit_code={r.get('exit_code')}")
    if r.get("oom_kills"):
        out.append(f"oom_kills={r.get('oom_kills')}")
    if r.get("integrity_failures"):
        out.append(f"integrity_failures={r.get('integrity_failures')}")
    if r.get("authority_violations"):
        out.append(f"authority_violations={r.get('authority_violations')}")
    ev = r.get("evidence") or {}
    if ev.get("verdict") != "VALID" or ev.get("profile") != "W1":
        out.append(f"evidence={ev.get('verdict')}/{ev.get('profile')}")
    held = ((r.get("checkpoints") or {}).get("evidence") or {}).get("held_privately_max")
    if held != 0:
        out.append(f"held_privately_max={held}")
    return out


def summarize(manifest: dict, seeds=SEEDS, require_cgroup="v2") -> dict:
    rows = manifest["summary"]["vre15"]
    host = manifest.get("host", {})
    applicable = require_cgroup is None or host.get("cgroup") == require_cgroup
    keys = Counter((r.get("seed"), r.get("policy")) for r in rows)
    expected = {(s, a) for s in seeds for a in ARMS}
    duplicates = sorted(k for k, n in keys.items() if n > 1)
    missing = sorted(expected - set(keys))
    unexpected = sorted(set(keys) - expected)
    complete = not (duplicates or missing or unexpected)
    by = {(r.get("seed"), r.get("policy")): r for r in rows}

    per_seed, better, worse, usable = [], 0, 0, []
    for s in seeds:
        n, c = by.get((s, "native")), by.get((s, "mapped-ckpt"))
        nv = residency(n) if n and ok_row(n) else None
        cv = residency(c) if c and ok_row(c) else None
        if nv is None or cv is None:
            status = "unusable"
        elif cv < nv:
            status, better = "better", better + 1
        elif cv > nv:
            status, worse = "worse", worse + 1
        else:
            status = "tie"
        if status != "unusable":
            usable.append((nv, cv))
        per_seed.append([s, nv, cv, status])
    t1 = "confirmed" if applicable and complete and better >= MIN_BETTER else "not confirmed"

    t2_rows = [[s, t2_failures(by.get((s, "mapped-ckpt")))] for s in seeds]
    t2_ok = applicable and complete and all(not f for _, f in t2_rows)

    def arm(policy):
        rs = [r for r in rows if r.get("policy") == policy]
        done = [r for r in rs if ok_row(r)]
        ic = [r["by_class"]["interactive"] for r in done if r.get("by_class", {}).get("interactive")]
        zero5 = [((r.get("revoked_residency") or {}).get("5.0") or {}).get("zero_fraction") for r in done]
        zero5 = [z for z in zero5 if z is not None]
        return {"runs": len(rs), "completed": len(done), "oom_killed": sum(1 for r in rs if r.get("oom_kills")),
                "interactive_within_deadline_mean": round(st.mean(x["within_deadline_pct"] for x in ic), 3) if ic else None,
                "p99_ms_median": round(st.median(x["p99_ms"] for x in ic), 2) if ic else None,
                "revoked_nothing_resident_at_5s_median": round(st.median(zero5), 3) if zero5 else None,
                "integrity_failures": sum(r.get("integrity_failures") or 0 for r in done),
                "authority_violations": sum(r.get("authority_violations") or 0 for r in done)}

    return {
        "run_id": manifest.get("run_id"), "host": host, "code": manifest.get("code"),
        "analyzer_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "applicable": applicable, "required_cgroup": require_cgroup,
        "complete": complete, "missing": missing, "duplicates": duplicates, "unexpected": unexpected,
        "T1_residency_follows_authority": {
            "seeds": len(seeds), "mapped_ckpt_better": better, "worse": worse,
            "ties": sum(1 for *_, st_ in per_seed if st_ == "tie"),
            "unusable": sum(1 for *_, st_ in per_seed if st_ == "unusable"),
            "required_better": MIN_BETTER, "outcome": t1,
            "reported_sign_test_p_over_usable_pairs": round(sign_test(better, worse), 4),
            "median_mean_resident_pages_5s": {
                "native": round(st.median(n for n, _ in usable), 2) if usable else None,
                "mapped_ckpt": round(st.median(c for _, c in usable), 2) if usable else None},
            "per_seed": per_seed},
        "T2_evidence_valid_every_run": {"runs_required": len(seeds),
                                        "outcome": "confirmed" if t2_ok else "not confirmed",
                                        "per_seed_failures": t2_rows},
        "reported": {"native": arm("native"), "mapped_ckpt": arm("mapped-ckpt")},
    }


def main(argv):
    if len(argv) != 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        print(json.dumps(summarize(json.load(f)), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
