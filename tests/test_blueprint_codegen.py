"""Generated pipelines are governed by construction, and actually run against the mount contract."""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from cappo_backend.blueprint.codegen import blueprint_to_pipeline, compile_pipeline
from cappo_backend.blueprint.compiler import compile_blueprint
from cappo_backend.capability_mount.service import GOVERNED_COUNTER_PACKAGE

CATALOG = [GOVERNED_COUNTER_PACKAGE]


def _python(intent: str) -> dict:
    return compile_pipeline(blueprint_to_pipeline(compile_blueprint(intent, CATALOG)))


class _FakeCappo:
    """Records the calls a generated pipeline makes; answers like CAPPO's mount routes."""

    def __init__(self, deny: set[str] = frozenset()):
        self.calls: list[tuple[str, dict]] = []
        self.deny = deny
        self.n = 0

    def __call__(self, *args, **kwargs):  # stands in for httpx.Client(...)
        return self

    def post(self, path, json=None):
        self.calls.append((path, json))
        if path.endswith("/capability/mounts"):
            self.n += 1
            body = {"decision": "allow", "token": {"mount_id": f"mnt_{self.n}", "token_id": f"tok_{self.n}", "nonce": "n"}}
        elif path.endswith("/actions"):
            denied = json["action"] in self.deny
            body = {"decision": "deny" if denied else "allow", "reason": "blocked" if denied else "allowed",
                    "anchoring": {"anchor_id": "a"}}
        else:
            body = {"decision": "deny", "reason": "terminated"}
        return types.SimpleNamespace(raise_for_status=lambda: types.SimpleNamespace(json=lambda: body))


def _run(code: str, fake: _FakeCappo, monkeypatch) -> dict:
    monkeypatch.setenv("VEKLOM_TOKEN", "t")
    monkeypatch.setenv("VEKLOM_WORKSPACE", "ws")
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(Client=fake))
    namespace: dict = {"__name__": "generated"}
    exec(compile(code, "pipeline.py", "exec"), namespace)
    return namespace


def test_each_step_mounts_exactly_its_action_asks_then_terminates(monkeypatch):
    result = _python("read the counter and then increment the counter")
    assert result["success"] and result["warnings"] == []
    fake = _FakeCappo()
    _run(result["python_code"], fake, monkeypatch)["main"]()
    paths = [p.rsplit("/", 1)[-1] if "/mnt_" in p else p for p, _ in fake.calls]
    assert paths == ["/api/capi/interlink/capability/mounts", "actions", "terminate"] * 2
    mounts = [body for path, body in fake.calls if path.endswith("/capability/mounts")]
    assert mounts[0]["requested_action_scope"] == {"reads": ["counter.read"], "writes": [], "blocked": []}
    assert mounts[1]["requested_action_scope"] == {"reads": [], "writes": ["counter.increment"], "blocked": []}


def test_effect_runs_only_on_allow_and_mount_is_terminated_on_deny(monkeypatch):
    result = _python("reset the counter")
    fake = _FakeCappo(deny={"counter.reset"})
    ns = _run(result["python_code"], fake, monkeypatch)
    ran = []
    step = next(v for k, v in ns.items() if k.startswith("step_step"))
    with pytest.raises(ns["Denied"]):
        step(effect=lambda: ran.append(True))
    assert ran == []
    assert fake.calls[-1][0].endswith("/terminate")
    assert any("blocked" in w for w in result["warnings"])


def test_uncovered_step_refuses_to_run(monkeypatch):
    result = _python("delete every customer record")
    ns = _run(result["python_code"], _FakeCappo(), monkeypatch)
    with pytest.raises(ns["Uncovered"]):
        ns["main"]()
    assert any("no capability covers" in w for w in result["warnings"])


def test_independent_steps_share_a_parallel_level():
    graph = {"nodes": [{"id": "a", "node_type": "read", "config": {"package_ref": "p@v1", "action": "x.read"}},
                       {"id": "b", "node_type": "read", "config": {"package_ref": "p@v1", "action": "y.read"}},
                       {"id": "c", "node_type": "write", "config": {"package_ref": "p@v1", "action": "z.write"}}],
             "edges": [{"source_node_id": "a", "target_node_id": "c"}, {"source_node_id": "b", "target_node_id": "c"}]}
    result = compile_pipeline(graph)
    assert result["parallel_levels"] == [["a", "b"], ["c"]]
    assert "ThreadPoolExecutor() as pool" in result["python_code"]


def test_cycle_is_reported_not_compiled():
    graph = {"nodes": [{"id": "a", "node_type": "read"}, {"id": "b", "node_type": "read"}],
             "edges": [{"source_node_id": "a", "target_node_id": "b"}, {"source_node_id": "b", "target_node_id": "a"}]}
    result = compile_pipeline(graph)
    assert result["success"] is False and "cycle" in result["warnings"][0]


def test_hostile_labels_cannot_inject_code():
    graph = {"nodes": [{"id": "x\nimport os", "node_type": "read", "label": '"""\nos.system("boom")\n"""',
                        "config": {"package_ref": 'p@v1"); os.system("boom', "action": "a.read"}}], "edges": []}
    code = compile_pipeline(graph)["python_code"]
    assert 'os.system("boom")' not in code.replace('\\"', '"').split("governed_step(")[0][-200:]
    import ast
    tree = ast.parse(code)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "system"]
    assert calls == []


def test_routes_return_python(client: TestClient):
    plan = client.post("/api/v1/gpc/compile", json={"intent": "read the counter"}).json()
    assert plan["python"]["success"] is True
    assert plan["pipeline"]["nodes"][0]["node_type"] == "intent"
    edited = client.post("/api/v1/gpc/pipeline/compile", json={"graph": plan["pipeline"]})
    assert edited.status_code == 200 and edited.json()["python_code"] == plan["python"]["python_code"]
