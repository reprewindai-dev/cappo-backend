"""VRE-1.5: the trace model is fixed and honest; committed runs are clean."""

import json
import pathlib

from vre15 import traces
from vre15.runner import KERNEL_PAGED

RUNS = pathlib.Path(__file__).resolve().parents[1] / "vre15" / "runs"


def test_trace_is_deterministic_for_a_seed():
    a = traces.make("informative", 50, 30, 100, 7)
    b = traces.make("informative", 50, 30, 100, 7)
    assert a == b
    assert traces.make("informative", 50, 30, 100, 8) != a


def test_only_out_of_window_requests_are_unauthorized():
    t = traces.make("informative", 80, 60, 150, 3)
    for at, tenant, intended in t["requests"]:
        assert traces.authorized(t["envelopes"][tenant], at) == intended
    assert any(not r[2] for r in t["requests"])


def test_control_trace_carries_no_envelope_information():
    t = traces.make("uninformative", 80, 60, 150, 3)
    assert {e["class"] for e in t["envelopes"]} == {"interactive"}
    assert all(e["windows"] == [[0.0, 60]] for e in t["envelopes"])
    assert all(r[2] for r in t["requests"])


def test_requests_are_time_ordered():
    t = traces.make("informative", 40, 30, 80, 1)
    times = [r[0] for r in t["requests"]]
    assert times == sorted(times)


def test_committed_runs_are_clean():
    for manifest in sorted(RUNS.glob("*/manifest.json")):
        rows = json.loads(manifest.read_text())["summary"]["vre15"]
        if not any("evidence" in r for r in rows):        # evidence runs measure cost, not a comparison
            assert {"native"} <= {r["policy"] for r in rows}
        for r in rows:
            if r["exit_code"] != 0:
                # Kept, never dropped: an OOM kill of a kernel-paged policy is
                # the kernel's reclaim failing under the limit, a real result.
                # A managed policy must hold its own budget, so a kill there,
                # or any failure that is not an OOM kill, is a defect.
                assert r["oom_kills"] >= 1 and r["policy"] in KERNEL_PAGED, (manifest, r)
                continue
            assert r["oom_kills"] == 0, (manifest, r["policy"], r["variant"])
            if r["policy"] in KERNEL_PAGED:
                # A kernel-paged policy never snapshots or restores a tenant
                # after warm-up; a fall-through into the snapshot policies'
                # housekeeping would show up here.
                assert r["demotions"] == 0 and r["restores"] == 0, (manifest, r["policy"])
            assert r["authority_violations"] == 0 and r["integrity_failures"] == 0, r


def test_paired_sign_test_and_pairing():
    from vre15 import paired

    assert paired.sign_test(5, 0) == 0.0625
    assert paired.sign_test(4, 1) == 0.375
    assert paired.sign_test(0, 0) == 1.0

    def row(policy, seed, pct, p99, exit_code=0):
        return {"budget_mib": 16, "variant": "informative", "seed": seed, "policy": policy,
                "swap_pages_in": 0, "exit_code": exit_code, "oom_kills": int(exit_code != 0),
                "by_class": {"interactive": {"within_deadline_pct": pct, "p99_ms": p99}}}

    rows = [row("native", 1, 98.0, 200), row("x", 1, 99.0, 10),
            row("native", 2, 99.0, 50), row("x", 2, 99.0, 60),
            row("native", 3, 99.5, 20), row("x", 3, 99.0, 30),
            row("native", 4, None, None, exit_code=-9), row("x", 4, 99.9, 5)]
    (r,) = paired.table(rows, "native")
    assert (r["wins"], r["losses"], r["ties"]) == (1, 1, 1)
    assert r["seeds"] == [1, 2, 3]
    assert r["failed"] == [{"seed": 4, "policy": "native", "exit_code": -9, "oom_kills": 1}]
    assert r["median_pct_diff"] == 0.0
    assert r["median_p99_diff_ms"] == 10


def test_oracle_bound_properties():
    from vre15 import oracle

    for variant in ("informative", "uninformative"):
        trace = traces.make(variant, 40, 30, 120, 5)
        refs = oracle.served_refs(trace, 5)
        envs = trace["envelopes"]
        for cap in (20, 80, 300):
            lru = oracle.run_lru(refs, cap)
            env, _ = oracle.run_envelope(refs, cap, envs)
            envp, _ = oracle.run_envelope(refs, cap, envs, prefetch=True)
            envw, _ = oracle.run_envelope(refs, cap, envs, prefetch=True, ws_only=True)
            opt = oracle.run_opt(refs, cap)
            assert opt <= min(lru, env, envp), (variant, cap)
            if variant == "uninformative":
                # one window per tenant: no previous window, so no prefetch
                assert envw == env, cap
            if variant == "uninformative":
                # The control: no tenant is ever outside authority while
                # requests are served, so the envelope policy is LRU.
                assert env == lru, cap
    # With room for every hot page, every policy takes only cold misses.
    trace = traces.make("informative", 20, 30, 80, 2)
    refs = oracle.served_refs(trace, 2)
    cold = len({(t, p) for _, t, p in refs})
    assert oracle.run_lru(refs, 10_000) == oracle.run_opt(refs, 10_000) == cold


def test_window_analysis_finds_onset_and_window():
    from vre15 import window

    def row(budget, seed, policy, pct, faults=0):
        return {"budget_mib": budget, "seed": seed, "policy": policy, "exit_code": 0, "oom_kills": 0,
                "request_thread_major_faults": faults,
                "by_class": {"interactive": {"within_deadline_pct": pct, "p99_ms": 1.0}}}

    rows = []
    for s in (1, 2, 3):
        rows += [row(32, s, "native", 100.0), row(32, s, "x", 100.0),
                 row(16, s, "native", 99.5), row(16, s, "x", 99.8),
                 row(8, s, "native", 98.0), row(8, s, "x", 97.0)]
    rows.append(row(8, 4, "native", 0.0) | {"exit_code": -9, "oom_kills": 1})   # killed: ignored
    res = window.analyse(rows)
    assert res["onset_budget_mib"] == 16
    assert res["window"] == {"x": [16]}


def test_stall_episodes_count_runs_of_stall_seconds():
    from vre15.runner import stall_stats

    trace = {"requests": [[0.2, 0, True], [0.7, 1, True], [1.5, 0, True], [3.1, 2, True],
                          [4.0, 1, True], [4.9, 0, True]]}
    results = [[0, 5.0, "interactive", True],          # second 0: fine
               [1, 150.0, "interactive", True],        # second 0: stall
               [0, 101.0, "interactive", True],        # second 1: stall (same episode)
               [2, 900.0, "batch", True],              # batch, not counted
               [1, None, "denied", False],             # denied, not counted
               [0, 120.0, "interactive", True]]        # second 4: new episode
    s = stall_stats(results, trace)
    assert s["stall_buckets"] == [0, 1, 4]
    assert (s["stall_seconds"], s["stall_episodes"]) == (3, 2)


def test_committed_evidence_bundles_verify_offline():
    """Every committed revocation bundle verifies from the repository alone:
    chain, issuer-signed EEE envelopes, residency, and the witness head. The
    images were checked at run time (the row's verdict) and are not kept."""
    from vre15 import verify_evidence
    bundles = sorted(RUNS.glob("*/*.evidence.json"))
    assert bundles
    for b in bundles:
        trust = json.loads(pathlib.Path(str(b).replace(".evidence.json", ".trust.json")).read_text())
        v = verify_evidence.verify_bundle(json.loads(b.read_text()), trust, require_witness=True)
        assert v["verdict"] == "VALID" and v["profile"] == "W1", (b, v)
        row = next(r for r in json.loads((b.parent / "manifest.json").read_text())["summary"]["vre15"]
                   if "evidence" in r)
        assert row["evidence"]["verdict"] == "VALID" and row["evidence"]["revocations"] == v["revocations"]
        # CAPPO's own EEE verifier, written independently, agrees on every envelope.
        from cappo_backend.services.eee import EEEVerifier, VerificationVerdict
        from vdb_c1 import canon
        cappo = EEEVerifier({kid: canon.unb64u(pub) for kid, pub in trust["issuer_keys"].items()})
        for rec in json.loads(b.read_text())["journal"]:
            report = cappo.verify(rec["body"]["envelope"])
            assert report.verdict == VerificationVerdict.VALID_WITH_UNRESOLVED_REFS, (b, rec["seq"], report)
            assert report.reasons == ["REVOCATION_NOT_CHECKED"]
