from __future__ import annotations

from types import SimpleNamespace

import pytest

from cappo_backend.config import Settings
from cappo_backend.services.orchestrator import RunOrchestrator


def _orchestrator() -> RunOrchestrator:
    return RunOrchestrator(
        db=None,
        pgl=None,
        builder=None,
        executor=None,
        audit=None,
    )


def _run() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run-1",
        request_payload={"agent_id": "agent-1", "pgl_id": "pgl-1"},
    )


def test_external_capi_validation_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import cappo_backend.config as config

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: Settings(
            veklom_byos_backend_url="https://api.veklom.com/v1",
            capi_external_validation_enabled=False,
        ),
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("external cAPI should not be called")

    monkeypatch.setattr("httpx.Client", fail_if_called)

    _orchestrator().validate_with_capi(_run())


def test_external_capi_validation_calls_byos_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cappo_backend.config as config

    calls: list[dict[str, object]] = []

    class _Response:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            calls.append({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: Settings(
            veklom_byos_backend_url="https://api.veklom.com/v1",
            veklom_api_key="test-key",
            capi_external_validation_enabled=True,
        ),
    )
    monkeypatch.setattr("httpx.Client", _Client)

    _orchestrator().validate_with_capi(_run())

    assert calls == [
        {
            "url": "https://api.veklom.com/api/v1/capi/execute",
            "json": {
                "agent_id": "agent-1",
                "pgl_id": "pgl-1",
                "mission_id": "run-1",
                "target_protocol": "http",
                "action": "cappo.exec",
                "payload": {"agent_id": "agent-1", "pgl_id": "pgl-1"},
            },
            "headers": {"Authorization": "Bearer test-key"},
        }
    ]
