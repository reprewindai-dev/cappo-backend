"""VRE-1.5: the trace model is fixed and honest; committed runs are clean."""

import json
import pathlib

from vre15 import traces

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
        assert {r["policy"] for r in rows} == {"native", "idle-lru", "envelope", "predictive"}
        for r in rows:
            assert r["exit_code"] == 0 and r["oom_kills"] == 0, (manifest, r["policy"], r["variant"])
            assert r["authority_violations"] == 0 and r["integrity_failures"] == 0, r
