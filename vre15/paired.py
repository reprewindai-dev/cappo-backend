#!/usr/bin/env python3
"""Paired comparison of VRE-1.5 policies across seeds.

    python -m vre15.paired vre15/runs/<run_id>/manifest.json [--baseline native]

Every (budget, variant, seed) cell runs each policy on the same trace, so
policies are compared pairwise within a cell. For each budget and each
non-baseline policy, the script reports, over the seeds:

- the interactive within-deadline share and p99, per seed, for both;
- wins: seeds where the policy met more deadlines than the baseline (ties
  are counted separately);
- an exact two-sided sign test on the wins, ignoring ties. With five seeds,
  even 5 of 5 wins gives p = 0.0625, so five seeds alone can never reach
  p < 0.05. The test only says how surprising the split would be if the
  policies were the same;
- the median paired difference in deadlines met and in p99.

A run that did not complete (non-zero exit, an OOM kill, or no interactive
results) is not a data point for either side. Its seed is left out of that
pair and listed under `failed`, so a kill is reported, never silently
scored.

Standard library only, so the result can be recomputed from the committed
manifest anywhere.
"""

import argparse
import json
import statistics
from collections import defaultdict
from math import comb


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    p = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * p)


def table(rows, baseline):
    cells = defaultdict(dict)
    for r in rows:
        ic = r["by_class"].get("interactive", {})
        ok = r.get("exit_code") == 0 and not r.get("oom_kills") and ic.get("within_deadline_pct") is not None
        cells[(r["budget_mib"], r["variant"], r.get("seed"))][r["policy"]] = (
            (ic.get("within_deadline_pct"), ic.get("p99_ms"), r.get("swap_pages_in")) if ok
            else ("failed", r.get("exit_code"), r.get("oom_kills")))
    out = []
    groups = defaultdict(list)
    for (budget, variant, seed), pol in sorted(cells.items(), key=lambda kv: (-kv[0][0], kv[0][1], kv[0][2])):
        groups[(budget, variant)].append((seed, pol))
    for (budget, variant), seeds in groups.items():
        policies = sorted({p for _, pol in seeds for p in pol if p != baseline})
        for policy in policies:
            both = [(seed, pol[baseline], pol[policy]) for seed, pol in seeds
                    if baseline in pol and policy in pol]
            pairs = [x for x in both if x[1][0] != "failed" and x[2][0] != "failed"]
            failed = [{"seed": seed, "policy": who, "exit_code": v[1], "oom_kills": v[2]}
                      for seed, b, p in both for who, v in ((baseline, b), (policy, p)) if v[0] == "failed"]
            wins = sum(1 for _, b, p in pairs if p[0] > b[0])
            losses = sum(1 for _, b, p in pairs if p[0] < b[0])
            out.append({
                "budget_mib": budget, "variant": variant, "policy": policy, "baseline": baseline,
                "seeds": [s for s, _, _ in pairs],
                "baseline_pct": [b[0] for _, b, _ in pairs], "policy_pct": [p[0] for _, _, p in pairs],
                "baseline_p99": [b[1] for _, b, _ in pairs], "policy_p99": [p[1] for _, _, p in pairs],
                "baseline_swapin": [b[2] for _, b, _ in pairs], "policy_swapin": [p[2] for _, _, p in pairs],
                "wins": wins, "losses": losses, "ties": len(pairs) - wins - losses, "failed": failed,
                "sign_test_p": round(sign_test(wins, losses), 4),
                "median_pct_diff": round(statistics.median(p[0] - b[0] for _, b, p in pairs), 3) if pairs else None,
                "median_p99_diff_ms": round(statistics.median(p[1] - b[1] for _, b, p in pairs), 3) if pairs else None,
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--baseline", default="native")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    with open(args.manifest) as f:
        rows = json.load(f)["summary"]["vre15"]
    res = table(rows, args.baseline)
    if args.json:
        print(json.dumps(res, indent=1))
        return
    for r in res:
        print(f"{r['budget_mib']:>4} MiB {r['variant']:<13} {r['policy']:<15} vs {r['baseline']}: "
              f"wins {r['wins']} / losses {r['losses']} / ties {r['ties']}  sign p={r['sign_test_p']}  "
              f"median diff {r['median_pct_diff']} pts, p99 {r['median_p99_diff_ms']} ms")
        for f in r["failed"]:
            print(f"{'':>9}FAILED seed {f['seed']} {f['policy']}: exit {f['exit_code']}, oom kills {f['oom_kills']}")
        print(f"{'':>9}{'baseline %':<14}{r['baseline_pct']}  p99 {r['baseline_p99']}")
        print(f"{'':>9}{'policy %':<14}{r['policy_pct']}  p99 {r['policy_p99']}")


if __name__ == "__main__":
    main()
