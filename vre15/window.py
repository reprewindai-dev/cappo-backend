#!/usr/bin/env python3
"""Threshold window: where along the budget axis does any policy beat native?

    python -m vre15.window vre15/runs/<run_id>/manifest.json [--onset 99.9]

For each budget in a multi-seed manifest:
- native's interactive within-deadline share (mean and min over seeds),
  its p99 and its request-thread major faults: how far native has
  degraded;
- for every other policy, paired against native seed by seed: wins,
  losses, ties, the median difference in within-deadline share, the
  median difference in p99, and the median difference in request-thread
  major faults.

Then two lines:
- onset: the largest budget at which native's mean share falls below
  `--onset` percent, i.e. where native starts to degrade;
- window: the budgets at which a policy wins more seeds than it loses with
  a positive median difference.

This is exploratory: with a few seeds a "window" is a place to look, not a
result. Any window found here needs a pre-registered confirmation on fresh
seeds before it is claimed. Standard library only.
"""

import argparse
import json
import statistics as st
from collections import defaultdict


def cells(rows):
    out = defaultdict(dict)                         # (budget, seed) -> policy -> row
    for r in rows:
        if r.get("exit_code") == 0 and not r.get("oom_kills") and r["by_class"].get("interactive"):
            out[(r["budget_mib"], r.get("seed"))][r["policy"]] = r
    return out


def ic(r, key):
    return r["by_class"]["interactive"][key]


def analyse(rows, baseline="native", onset=99.9):
    c = cells(rows)
    budgets = sorted({b for b, _ in c}, reverse=True)
    policies = sorted({p for v in c.values() for p in v if p != baseline})
    report = []
    for b in budgets:
        seeds = [s for (bb, s) in c if bb == b]
        base = [c[(b, s)][baseline] for s in seeds if baseline in c[(b, s)]]
        entry = {"budget_mib": b, "seeds": len(seeds),
                 "native_pct_mean": round(st.mean(ic(r, "within_deadline_pct") for r in base), 3) if base else None,
                 "native_pct_min": min(ic(r, "within_deadline_pct") for r in base) if base else None,
                 "native_p99_median": round(st.median(ic(r, "p99_ms") for r in base), 2) if base else None,
                 "native_req_faults_median": st.median(r.get("request_thread_major_faults") or 0 for r in base)
                 if base else None,
                 "policies": {}}
        for p in policies:
            pairs = [(c[(b, s)][baseline], c[(b, s)][p]) for s in seeds
                     if baseline in c[(b, s)] and p in c[(b, s)]]
            if not pairs:
                continue
            d = [ic(y, "within_deadline_pct") - ic(x, "within_deadline_pct") for x, y in pairs]
            entry["policies"][p] = {
                "n": len(pairs), "wins": sum(v > 0 for v in d), "losses": sum(v < 0 for v in d),
                "median_pct_diff": round(st.median(d), 3),
                "median_p99_diff_ms": round(st.median(ic(y, "p99_ms") - ic(x, "p99_ms") for x, y in pairs), 2),
                "median_req_fault_diff": st.median((y.get("request_thread_major_faults") or 0)
                                                   - (x.get("request_thread_major_faults") or 0) for x, y in pairs),
            }
        report.append(entry)
    degraded = [e["budget_mib"] for e in report if e["native_pct_mean"] is not None and e["native_pct_mean"] < onset]
    window = {p: [e["budget_mib"] for e in report if p in e["policies"]
                  and e["policies"][p]["wins"] > e["policies"][p]["losses"]
                  and e["policies"][p]["median_pct_diff"] > 0] for p in policies}
    return {"onset_budget_mib": max(degraded) if degraded else None, "onset_threshold_pct": onset,
            "window": window, "budgets": report}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--baseline", default="native")
    ap.add_argument("--onset", type=float, default=99.9)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    with open(a.manifest) as f:
        rows = json.load(f)["summary"]["vre15"]
    res = analyse(rows, a.baseline, a.onset)
    if a.json:
        print(json.dumps(res, indent=1))
        return
    for e in res["budgets"]:
        print(f"{e['budget_mib']:>3} MiB  native {e['native_pct_mean']}% (min {e['native_pct_min']}) "
              f"p99 {e['native_p99_median']} ms  req faults {e['native_req_faults_median']}  [{e['seeds']} seeds]")
        for p, v in e["policies"].items():
            print(f"         {p:<18} {v['wins']}W/{v['losses']}L of {v['n']}  "
                  f"Δpct {v['median_pct_diff']:+}  Δp99 {v['median_p99_diff_ms']:+} ms  "
                  f"Δreq faults {v['median_req_fault_diff']:+}")
    print(f"onset (native mean < {res['onset_threshold_pct']}%): largest such budget = {res['onset_budget_mib']} MiB")
    for p, w in res["window"].items():
        print(f"window {p}: {w if w else 'none'}")


if __name__ == "__main__":
    main()
