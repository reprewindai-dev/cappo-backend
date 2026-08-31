from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cappo_backend.security.consequence_replay_prerequisite import (
    ConsequenceReplayPrerequisiteMiddleware,
)


def _app(*, production: bool, redis_client=None) -> FastAPI:
    app = FastAPI()
    app.state.redis_client = redis_client
    settings = SimpleNamespace(is_production=production)
    app.add_middleware(ConsequenceReplayPrerequisiteMiddleware, settings=settings)

    @app.post("/v1/exec")
    def exec_route():
        return {"reached": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def test_production_exec_fails_closed_without_shared_replay_store() -> None:
    response = TestClient(_app(production=True)).post("/v1/exec")

    assert response.status_code == 503
    assert response.json() == {
        "error": "WID_REPLAY_CACHE_UNAVAILABLE",
        "detail": "Production governed execution requires the shared replay cache.",
        "fail_closed": True,
    }


def test_non_exec_route_is_not_blocked_by_replay_prerequisite() -> None:
    response = TestClient(_app(production=True)).get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_development_exec_may_use_existing_local_replay_behavior() -> None:
    response = TestClient(_app(production=False)).post("/v1/exec")

    assert response.status_code == 200
    assert response.json() == {"reached": True}


def test_production_exec_proceeds_when_shared_replay_client_is_configured() -> None:
    response = TestClient(_app(production=True, redis_client=object())).post("/v1/exec")

    assert response.status_code == 200
    assert response.json() == {"reached": True}
