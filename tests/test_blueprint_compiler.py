"""The blueprint compiler reports what the live catalog covers; it never approves."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cappo_backend.blueprint.compiler import compile_blueprint
from cappo_backend.capability_mount.models import CapabilityPackage
from cappo_backend.capability_mount.service import GOVERNED_COUNTER_PACKAGE

CATALOG = [GOVERNED_COUNTER_PACKAGE]


def _kinds(plan: dict) -> list[tuple[str | None, str]]:
    return [(step["action"], step["kind"]) for step in plan["steps"]]


def test_covered_steps_bind_to_catalog_actions_and_are_ready_for_mount():
    plan = compile_blueprint("show the counter and then bump the counter", CATALOG)
    assert _kinds(plan) == [("counter.read", "read"), ("counter.increment", "write")]
    assert plan["verdict"] == "ready_for_mount"
    assert plan["grants_authority"] is False
    assert plan["suggested_mounts"] == [{
        "package_ref": "veklom.governed-counter@v1",
        "reads": ["counter.read"],
        "writes": ["counter.increment"],
        "blocked": ["counter.reset"],
    }]


def test_a_clause_without_an_object_borrows_it_from_its_neighbour():
    plan = compile_blueprint("read and increment the counter", CATALOG)
    assert _kinds(plan) == [("counter.read", "read"), ("counter.increment", "write")]


def test_blocked_action_is_reported_blocked_never_downgraded():
    plan = compile_blueprint("reset the counter", CATALOG)
    assert _kinds(plan) == [("counter.reset", "blocked")]
    assert plan["verdict"] == "blocked"


def test_step_no_capability_covers_is_a_gap_and_the_plan_is_incomplete():
    plan = compile_blueprint("increment the counter, then delete every customer record", CATALOG)
    assert _kinds(plan) == [("counter.increment", "write"), (None, "uncovered")]
    assert "consequential" in plan["steps"][1]["reason"]
    assert plan["verdict"] == "incomplete"


def test_no_template_output_and_no_automatic_approval():
    plan = compile_blueprint("send the quarterly report to the board", CATALOG)
    assert plan["verdict"] == "incomplete"
    assert "policy_result" not in plan
    assert all(node["type"] != "quantum" for node in plan["graph"]["nodes"])


def test_same_intent_and_catalog_compile_to_the_same_plan_id_and_hash():
    a = compile_blueprint("read the counter", CATALOG)
    b = compile_blueprint("read   the counter", CATALOG)
    assert (a["id"], a["proof_hash"]) == (b["id"], b["proof_hash"])


def test_a_catalog_change_changes_the_hash():
    other = CapabilityPackage(
        id="acme.tickets@v1", family="support", title="Tickets", purpose="Read support tickets.",
        reads=["ticket.read"],
    )
    a = compile_blueprint("read the counter", CATALOG)
    b = compile_blueprint("read the counter", [*CATALOG, other])
    assert a["catalog_digest"] != b["catalog_digest"]
    assert a["proof_hash"] != b["proof_hash"]


def test_graph_mounts_each_package_once_before_its_first_step():
    plan = compile_blueprint("read the counter and increment the counter", CATALOG)
    ids = [node["id"] for node in plan["graph"]["nodes"]]
    assert ids == ["intent", "mount:veklom.governed-counter@v1", "step_1", "step_2"]
    assert plan["graph"]["edges"] == [
        {"from": "intent", "to": "mount:veklom.governed-counter@v1"},
        {"from": "mount:veklom.governed-counter@v1", "to": "step_1"},
        {"from": "step_1", "to": "step_2"},
    ]


def test_empty_intent_is_rejected_by_the_route(client: TestClient):
    assert client.post("/api/v1/gpc/compile", json={"intent": ""}).status_code == 422


def test_route_compiles_against_the_served_catalog(client: TestClient):
    response = client.post("/api/v1/gpc/compile", json={"intent": "read the counter"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "compiled"
    assert body["grants_authority"] is False
    assert body["verdict"] in {"ready_for_mount", "incomplete"}  # depends on the catalog the app registered
