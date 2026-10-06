"""Credit metering at the CAPPO governance point (LockerPhycer faked in-process)."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import GovernedCounterAdapter, TargetAdapterRegistry
from cappo_backend.capability_mount.service import GOVERNED_COUNTER_PACKAGE, AnchorResult
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.services.entitlement_meter import EntitlementMeter

COSTS = {"verification_read": 5, "standard_governed_action": 15, "governed_execution": 25}


class ConfirmedAnchor:
    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id=f"anchor-{event_type}-{id(payload)}")


class FakeLockerPhycer:
    def __init__(self, balance: int = 1000, down: bool = False) -> None:
        self.balance, self.down = balance, down
        self.debits: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.reversed: list[str] = []
        self.events: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("lockerphycer down", request=request)
        assert request.headers["X-Veklom-Service-Token"] == "svc"
        body = json.loads(request.content)
        path = request.url.path
        self.calls.append((path, body))
        if path.endswith("/entitlements/meter"):
            key, cost = body["idempotency_key"], COSTS[body["action_type"]]
            if key in self.debits:
                return httpx.Response(200, json={"allowed": True, "charged": True, "replay": True,
                                                 "credits": cost, "entry_id": key})
            if self.balance < cost:
                if body["action_type"] == "verification_read":
                    return httpx.Response(200, json={"allowed": True, "charged": False, "replay": False, "credits": 0})
                return httpx.Response(402, json={"error": {"code": "CREDITS_EXHAUSTED", "plan": "developer",
                                                           "balance": {"total_available": self.balance},
                                                           "upgrade_path": None}})
            self.balance -= cost
            self.debits[key] = body
            return httpx.Response(200, json={"allowed": True, "charged": True, "replay": False,
                                             "credits": cost, "entry_id": key})
        if path.endswith("/entitlements/reverse"):
            key = body["idempotency_key"]
            if key in self.debits and key not in self.reversed:
                self.reversed.append(key)
                self.balance += COSTS[self.debits[key]["action_type"]]
            return httpx.Response(200, json={"reversed": key in self.reversed})
        if path.endswith("/activation-events"):
            self.events.append(body["event_name"])
            return httpx.Response(200, json={"recorded": True})
        return httpx.Response(404)


def _install(client: TestClient, tmp_path: Path, lp: FakeLockerPhycer) -> GovernedCounterAdapter:
    registry = client.app.state.mount_registry
    registry.register_package(GOVERNED_COUNTER_PACKAGE)
    registry.anchor = ConfirmedAnchor()
    adapter = GovernedCounterAdapter(tmp_path)
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(GovernedCounterAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"
    cfg = SimpleNamespace(entitlements_metering="enforce", entitlements_url="http://lp.test",
                          entitlements_service_token="svc", entitlements_timeout_ms=1000)
    meter = EntitlementMeter(cfg, transport=httpx.MockTransport(lp.handler))
    meter.emit = functools.partial(EntitlementMeter.emit, meter, background=False)  # deterministic
    client.app.state.entitlement_meter = meter
    return adapter


def _mount(client: TestClient) -> dict:
    r = client.post("/v1/capability/mounts", json={
        "package_ref": GOVERNED_COUNTER_PACKAGE.id,
        "execution_scope": {"workspace": "w1", "project": "p1"},
        "requested_action_scope": {"reads": ["counter.read"], "writes": ["counter.increment"],
                                   "blocked": ["counter.reset"]},
        "ttl_seconds": 300,
    })
    assert r.status_code == 200 and r.json()["decision"] == "allow", r.text
    return r.json()


def _execute(client: TestClient, mount: dict, action: str = "counter.increment", op: str = "op-1"):
    token = mount["token"]
    return client.post(f"/v1/capability/mounts/{mount['mount']['id']}/execute", json={
        "token_id": token["token_id"], "nonce": token["nonce"], "action": action,
        "target_ref": GovernedCounterAdapter.ref, "resource": "demo-1", "arguments": {}, "operation_id": op,
    })


def test_execution_charged_once_with_evidence(client: TestClient, db: Session, tmp_path: Path) -> None:
    lp = FakeLockerPhycer()
    adapter = _install(client, tmp_path, lp)
    mount = _mount(client)
    first = _execute(client, mount)
    assert first.status_code == 200 and first.json()["decision"] == "allow"
    meter_calls = [b for p, b in lp.calls if p.endswith("/meter")]
    assert len(meter_calls) == 1
    call = meter_calls[0]
    assert call["action_type"] == "governed_execution" and call["workspace_id"] == "w1"
    assert call["idempotency_key"] == f"cappo:execute:{mount['mount']['id']}:{mount['token']['token_id']}"
    assert lp.balance == 1000 - 25

    replay = _execute(client, mount)  # same operation id -> CAPPO idempotency replay
    assert replay.json()["decision"] == "deny"
    assert len([1 for p, _ in lp.calls if p.endswith("/meter")]) == 1  # never double-charged
    assert adapter.invocation_count == 1

    evidence = db.execute(select(AuditEvent).where(AuditEvent.operation_type == "credits_metered")).scalars().all()
    assert len(evidence) == 1
    payload = evidence[0].payload
    assert payload["credits"] == 25 and payload["charged"] is True
    assert payload["receipt_id"] == first.json()["consequence"]["receipt_id"]
    assert evidence[0].run_id == first.json()["authority"]["execution_id"]
    assert isinstance(payload["duration_ms"], float)
    assert {"capability_issued", "first_authority_decision", "first_governed_execution"} <= set(lp.events)


def test_exhausted_credits_return_structured_402_before_authority(client: TestClient, tmp_path: Path) -> None:
    lp = FakeLockerPhycer(balance=10)
    adapter = _install(client, tmp_path, lp)
    mount = _mount(client)
    denied = _execute(client, mount)
    assert denied.status_code == 402
    detail = denied.json()["detail"]
    assert detail["code"] == "CREDITS_EXHAUSTED" and detail["plan"] == "developer"
    assert adapter.invocation_count == 0
    status = client.get(f"/v1/capability/mounts/{mount['mount']['id']}").json()
    assert status["nonce_consumed"] is False  # commercial denial consumes no authority
    lp.balance = 100  # funded
    again = _execute(client, mount)
    assert again.status_code == 200 and again.json()["decision"] == "allow"
    assert "first_denied_action" not in lp.events  # a commercial denial is not an authority decision


def test_authority_denial_reverses_the_charge(client: TestClient, tmp_path: Path) -> None:
    lp = FakeLockerPhycer()
    _install(client, tmp_path, lp)
    mount = _mount(client)
    token = mount["token"]
    r = client.post(f"/v1/capability/mounts/{mount['mount']['id']}/actions", json={
        "token_id": token["token_id"], "nonce": token["nonce"], "action": "counter.reset", "resource": "demo-1"})
    assert r.json()["decision"] == "deny"
    assert len(lp.reversed) == 1 and lp.balance == 1000
    assert "first_denied_action" in lp.events


def test_meter_down_fails_closed_for_execution_and_safe_for_reads(client: TestClient, tmp_path: Path) -> None:
    lp = FakeLockerPhycer()
    adapter = _install(client, tmp_path, lp)
    mount = _mount(client)
    lp.down = True
    r = _execute(client, mount)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "METERING_UNAVAILABLE"
    assert adapter.invocation_count == 0
    read = client.get(f"/v1/capability/targets/{GovernedCounterAdapter.ref}/state",
                      params={"resource": "demo-1", "mount_id": mount["mount"]["id"]})
    assert read.status_code == 200  # verification reads fail safe


def test_verification_read_is_metered(client: TestClient, tmp_path: Path) -> None:
    lp = FakeLockerPhycer()
    _install(client, tmp_path, lp)
    mount = _mount(client)
    read = client.get(f"/v1/capability/targets/{GovernedCounterAdapter.ref}/state",
                      params={"resource": "demo-1", "mount_id": mount["mount"]["id"]})
    assert read.status_code == 200
    assert [b["action_type"] for p, b in lp.calls if p.endswith("/meter")] == ["verification_read"]
    assert lp.balance == 995


def test_metering_off_by_default_makes_no_calls(client: TestClient, tmp_path: Path) -> None:
    registry = client.app.state.mount_registry
    registry.register_package(GOVERNED_COUNTER_PACKAGE)
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(GovernedCounterAdapter.ref, GovernedCounterAdapter(tmp_path))
    client.headers["X-Workspace-ID"] = "w1"
    mount = _mount(client)
    assert _execute(client, mount).json()["decision"] == "allow"
    assert client.app.state.entitlement_meter.enabled is False
