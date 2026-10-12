#!/usr/bin/env python3
"""Pre-registered transfer test (WSL2 / cgroup v2): the verdict, from a manifest.

    python -m vre15.transfer vre15/runs/<run_id>/manifest.json

Prints one short JSON summary, small enough to paste. It holds the
pre-registered decisions (T1, T2; see vre15/README.md, "Pre-registered:
transfer to WSL2 / cgroup v2") and the reported-only measurements. The
per-seed values are included, so the test can be recomputed from the
summary alone.
"""

import json
import statistics as st
import sys

from vre15.paired import sign_test

ALPHA = 0.05


def ok_row(r):
    return r.get("exit_code") == 0 and not r.get("oom_kills")


def residency(r, at="5.0"):
    return ((r.get("revoked_residency") or {}).get(at) or {}).get("mean_pages")


def summarize(manifest: dict) -> dict:
    rows = manifest["summary"]["vre15"]
    by = {}
    for r in rows:
        by.setdefault(r["seed"], {})[r["policy"]] = r
    pairs = []
    for seed, arms in sorted(by.items()):
        n, c = arms.get("native"), arms.get("mapped-ckpt")
        if n and c and ok_row(n) and ok_row(c) and residency(n) is not None and residency(c) is not None:
            pairs.append((seed, residency(n), residency(c)))
    better = sum(c < n for _, n, c in pairs)
    worse = sum(c > n for _, n, c in pairs)
    p = sign_test(better, worse)
    t1 = "confirmed" if better > worse and p < ALPHA else "not confirmed"

    ckpt = [r for r in rows if r["policy"] == "mapped-ckpt" and ok_row(r)]
    verdicts = [(r["seed"], (r.get("evidence") or {}).get("verdict"), (r.get("evidence") or {}).get("profile"),
                 ((r.get("checkpoints") or {}).get("evidence") or {}).get("held_privately_max")) for r in ckpt]
    t2_ok = bool(verdicts) and all(v == "VALID" and prof == "W1" and held == 0 for _, v, prof, held in verdicts)

    def arm(policy):
        rs = [r for r in rows if r["policy"] == policy]
        done = [r for r in rs if ok_row(r)]
        ic = [r["by_class"]["interactive"] for r in done if r["by_class"].get("interactive")]
        zero5 = [((r.get("revoked_residency") or {}).get("5.0") or {}).get("zero_fraction") for r in done]
        zero5 = [z for z in zero5 if z is not None]
        return {"runs": len(rs), "completed": len(done), "oom_killed": sum(1 for r in rs if r.get("oom_kills")),
                "interactive_within_deadline_mean": round(st.mean(x["within_deadline_pct"] for x in ic), 3) if ic else None,
                "p99_ms_median": round(st.median(x["p99_ms"] for x in ic), 2) if ic else None,
                "revoked_nothing_resident_at_5s_median": round(st.median(zero5), 3) if zero5 else None,
                "integrity_failures": sum(r.get("integrity_failures") or 0 for r in done),
                "authority_violations": sum(r.get("authority_violations") or 0 for r in done)}

    return {
        "run_id": manifest["run_id"], "host": manifest["host"],
        "T1_residency_follows_authority": {
            "pairs": len(pairs), "mapped_ckpt_better": better, "worse": worse, "ties": len(pairs) - better - worse,
            "sign_test_p": round(p, 4), "alpha": ALPHA, "outcome": t1,
            "median_mean_resident_pages_5s": {"native": round(st.median(n for _, n, _ in pairs), 2) if pairs else None,
                                              "mapped_ckpt": round(st.median(c for _, _, c in pairs), 2) if pairs else None},
            "per_seed": [[s, n, c] for s, n, c in pairs]},
        "T2_evidence_valid_every_run": {"runs": len(verdicts), "outcome": "confirmed" if t2_ok else "not confirmed",
                                        "per_seed": verdicts},
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
