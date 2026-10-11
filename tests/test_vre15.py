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
