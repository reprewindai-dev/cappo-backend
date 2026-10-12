"""The pre-registered verdict of the independent residency audit.

    python -m vre15.audit_prereg WITH_WS_MANIFEST WITHOUT_WS_MANIFEST

The rules are fixed in vre15/README.md, "Pre-registered: independent
residency audit", committed before any run with these seeds. This module
applies them and nothing else. Two runs (vre15/runner.py --audit):
- with --record-ws: policies mapped and mapped-ckpt, seeds 991-995;
- without --record-ws: mapped-ckpt, the same seeds.

A1  the auditor finds the treatment clean: in every mapped-ckpt run of the
    first manifest, the bundle verifies VALID W1+A1 and every checkable gap
    after authority (at least MIN_CHECKABLE per run) had a sweep inside it.
A2  the auditor sees the control: in every mapped run, private pages in at
    least CONTROL_SHARE of the checkable gaps after authority.
A3  the warm-up gap and its cause: with --record-ws, private pages before
    the first window in at least WARMUP_SHARE of checkable pre-authority
    gaps, in both arms; without it, in none (mapped-ckpt, VALID W1+A1).
A run that did not complete (exit code, OOM kill) or is missing fails the
hypothesis it belongs to.
"""

import json
import sys

SEEDS = (991, 992, 993, 994, 995)
MIN_CHECKABLE = 100
CONTROL_SHARE = 0.5
WARMUP_SHARE = 0.5


def _audit(row):
    return (row.get("evidence") or {}).get("audit") if row["policy"] == "mapped-ckpt" else row.get("audit")


def _rows(manifest, policy):
    out = {}
    for r in manifest["summary"]["vre15"]:
        if r["policy"] == policy:
            out.setdefault(r["seed"], []).append(r)
    return out


def _complete(row):
    return row["exit_code"] == 0 and row["oom_kills"] == 0


def _treatment_ok(row):
    ev = row.get("evidence") or {}
    a = ev.get("audit") or {}
    after = a.get("after_authority", {})
    return (_complete(row) and ev.get("verdict") == "VALID" and ev.get("profile") == "W1+A1"
            and after.get("gaps_checkable", 0) >= MIN_CHECKABLE
            and after.get("gaps_checked") == after.get("gaps_checkable"))


def _share(part, whole):
    return part / whole if whole else 0.0


def summarize(with_ws: dict, without_ws: dict) -> dict:
    problems = []
    for name, m, ws in (("with_ws", with_ws, True), ("without_ws", without_ws, False)):
        p = m.get("params", {})
        if not p.get("audit") or bool(p.get("record_ws")) != ws or p.get("budget_mib") != [16]:
            problems.append(f"{name}: parameters differ from the pre-registration")
    a1, a2, a3 = {}, {}, {}
    ckpt, mapped, plain = _rows(with_ws, "mapped-ckpt"), _rows(with_ws, "mapped"), _rows(without_ws, "mapped-ckpt")
    for s in SEEDS:
        c, m, n = ckpt.get(s, []), mapped.get(s, []), plain.get(s, [])
        if len(c) != 1 or len(m) != 1 or len(n) != 1:
            a1[s] = a2[s] = a3[s] = False
            problems.append(f"seed {s}: expected one run per arm")
            continue
        c, m, n = c[0], m[0], n[0]
        a1[s] = _treatment_ok(c)
        ma = _audit(m) or {}
        after = ma.get("after_authority", {})
        a2[s] = (_complete(m) and ma.get("verdict") == "AUDIT_READ"
                 and after.get("gaps_checkable", 0) >= MIN_CHECKABLE
                 and _share(after.get("checkable_gaps_with_private_pages", 0), after["gaps_checkable"])
                 >= CONTROL_SHARE)
        warm = []
        for row in (c, m):
            before = (_audit(row) or {}).get("before_first_authority", {})
            warm.append(_complete(row) and before.get("gaps_checkable", 0) > 0 and _share(
                before.get("checkable_gaps_with_private_pages", 0), before["gaps_checkable"]) >= WARMUP_SHARE)
        before_plain = (_audit(n) or {}).get("before_first_authority", {})
        a3[s] = all(warm) and _treatment_ok(n) and before_plain.get("gaps_with_private_pages") == 0

    def outcome(per):
        return "confirmed" if not problems and all(per.values()) else "not confirmed"

    return {"A1_treatment_clean_under_independent_audit": {"outcome": outcome(a1), "per_seed": a1},
            "A2_auditor_sees_the_control": {"outcome": outcome(a2), "per_seed": a2},
            "A3_warmup_state_precedes_authority": {"outcome": outcome(a3), "per_seed": a3},
            "problems": problems,
            "runs": {"with_ws": with_ws.get("run_id"), "without_ws": without_ws.get("run_id")}}


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    with open(argv[1]) as f1, open(argv[2]) as f2:
        print(json.dumps(summarize(json.load(f1), json.load(f2))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
