"""The hostile-battery fixture routes must not exist in production.

/consequence/dispatch answers with a fixed mock transaction hash and a pinned revocation
epoch; /consequence/reconcile can simulate a target outage. Both were reachable on the
production router.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cappo_backend.api.routers import exec_router


def _request(is_production: bool) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(is_production=is_production))))


def test_fixture_routes_are_404_in_production() -> None:
    with pytest.raises(HTTPException) as refused:
        exec_router._hostile_battery_fixture_only(_request(True))
    assert refused.value.status_code == 404


def test_fixture_routes_remain_available_outside_production() -> None:
    assert exec_router._hostile_battery_fixture_only(_request(False)) is None


def test_both_fixture_routes_carry_the_guard() -> None:
    guarded = {
        route.path
        for route in exec_router.router.routes
        if any(dep.call is exec_router._hostile_battery_fixture_only for dep in route.dependant.dependencies)
    }
    assert {path for path in guarded if "/consequence/" in path} == {
        path for path in (r.path for r in exec_router.router.routes) if "/consequence/" in path
    }
    assert len(guarded) == 2
