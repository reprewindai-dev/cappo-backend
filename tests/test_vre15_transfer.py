"""vre15/transfer.py enforces the pre-registered transfer rules (Amendment 1)
exactly: completeness, 12 of 15 seeds, every mapped-ckpt run's evidence,
cgroup v2 only. Checked on synthetic manifests and on H15's own run."""

import json
import pathlib

from vre15 import transfer

RUNS = pathlib.Path(__file__).resolve().parents[1] / "vre15" / "runs"


def row(seed, policy, pages, ok=True, evidence=True):
    r = {"seed": seed, "policy": policy, "exit_code": 0 if ok else -9, "oom_kills": 0 if ok else 1,
         "integrity_failures": 0, "authority_violations": 0,
         "revoked_residency": {"5.0": {"mean_pages": pages, "zero_fraction": 0.5}},
         "by_class": {"interactive": {"within_deadline_pct": 99.0, "p99_ms": 5.0}}}
    if policy == "mapped-ckpt" and evidence:
        r["evidence"] = {"verdict": "VALID", "profile": "W1"}
        r["checkpoints"] = {"evidence": {"held_privately_max": 0}}
    return r


def manifest(rows, cgroup="v2"):
    return {"run_id": "t", "host": {"cgroup": cgroup}, "summary": {"vre15": rows}}


def full(better=15):
    rows = []
    for i, s in enumerate(transfer.SEEDS):
        rows.append(row(s, "native", 5.0))
        rows.append(row(s, "mapped-ckpt", 1.0 if i < better else 6.0))
    return rows


def verdicts(m, **kw):
    v = transfer.summarize(m, **kw)
    return v["T1_residency_follows_authority"]["outcome"], v["T2_evidence_valid_every_run"]["outcome"], v


def test_a_complete_clean_run_confirms_both():
    t1, t2, v = verdicts(manifest(full()))
    assert (t1, t2, v["complete"], v["applicable"]) == ("confirmed", "confirmed", True, True)


def test_t1_needs_twelve_of_fifteen_seeds():
    assert verdicts(manifest(full(better=12)))[0] == "confirmed"
    assert verdicts(manifest(full(better=11)))[0] == "not confirmed"


def test_failed_arms_count_against_t1_instead_of_shrinking_the_denominator():
    rows = full()
    for s in transfer.SEEDS[:4]:                              # 4 native runs OOM-killed: 11 usable wins
        rows[[i for i, r in enumerate(rows) if r["seed"] == s and r["policy"] == "native"][0]] = \
            row(s, "native", None, ok=False)
    t1, _, v = verdicts(manifest(rows))
    t = v["T1_residency_follows_authority"]
    assert (t1, t["mapped_ckpt_better"], t["unusable"]) == ("not confirmed", 11, 4)
    assert t["reported_sign_test_p_over_usable_pairs"] < 0.05  # the old rule would have confirmed this


def test_one_failed_or_unverified_mapped_ckpt_run_fails_t2():
    rows = full()
    i = next(i for i, r in enumerate(rows) if r["seed"] == 805 and r["policy"] == "mapped-ckpt")
    rows[i] = row(805, "mapped-ckpt", 1.0, ok=False)
    assert verdicts(manifest(rows))[1] == "not confirmed"
    rows = full()
    rows[i] = row(805, "mapped-ckpt", 1.0, evidence=False)
    _, t2, v = verdicts(manifest(rows))
    assert t2 == "not confirmed"
    assert dict(v["T2_evidence_valid_every_run"]["per_seed_failures"])[805]


def test_missing_duplicate_or_extra_rows_make_the_run_incomplete():
    rows = full()
    assert verdicts(manifest(rows[:-1]))[:2] == ("not confirmed", "not confirmed")
    assert verdicts(manifest(rows + [rows[0]]))[2]["duplicates"]
    assert verdicts(manifest(rows + [row(816, "native", 5.0)]))[2]["unexpected"]


def test_a_cgroup_v1_manifest_is_not_applicable():
    t1, t2, v = verdicts(manifest(full(), cgroup="v1"))
    assert (t1, t2, v["applicable"]) == ("not confirmed", "not confirmed", False)


def test_the_amended_rules_on_h15s_own_run():
    """H15 (seeds 701-715, cgroup v1): 13 wins of 15 meets T1's 12-of-15 bar;
    its 2 native warm-up kills count as not-better, as the amendment says."""
    m = json.loads((RUNS / "20261011T181835Z" / "manifest.json").read_text())
    m["summary"]["vre15"] = [r for r in m["summary"]["vre15"] if r["policy"] in transfer.ARMS]
    t1, _, v = verdicts(m, seeds=tuple(range(701, 716)), require_cgroup=None)
    t = v["T1_residency_follows_authority"]
    assert (t1, t["mapped_ckpt_better"], t["worse"], t["unusable"]) == ("confirmed", 13, 0, 2)
