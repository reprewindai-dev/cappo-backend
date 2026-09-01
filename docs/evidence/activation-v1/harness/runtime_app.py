"""Local-only runtime harness for Activation v1 evidence.

This module does not alter repository code.  It keeps the real CAPPO app,
middleware, routes, registry, and consequence implementation while supplying
the SQLite session behavior needed on a host without PostgreSQL RLS.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import ssl
import sys
from collections.abc import Iterator
from pathlib import Path

import h11
from fastapi import HTTPException
from sqlalchemy import text
from starlette.requests import Request
from uvicorn.protocols.http.h11_impl import (
    HIGH_WATER_LIMIT,
    H11Protocol,
    RequestResponseCycle,
    get_local_addr,
    get_remote_addr,
    is_ssl,
    service_unavailable,
    unquote,
)

import cappo_backend.models  # noqa: F401 — register all ORM tables
from cappo_backend.db.base import Base
from cappo_backend.db.session import SessionLocal, engine, get_session
from cappo_backend.main import app

HARNESS_ROOT = Path(
    os.environ.get("ACTIVATION_V1_ROOT", "/home/ubuntu/activation_v1")
)


class PeerCertificateH11Protocol(H11Protocol):
    """Expose the verified TLS peer certificate as the app's TLS extension."""

    def connection_made(self, transport: asyncio.Transport) -> None:
        super().connection_made(transport)
        ssl_object = transport.get_extra_info("ssl_object")
        der_certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        self._peer_certificate = (
            ssl.DER_cert_to_PEM_cert(der_certificate).encode("ascii")
            if der_certificate
            else None
        )

    def handle_events(self) -> None:
        while True:
            try:
                event = self.conn.next_event()
            except h11.RemoteProtocolError:
                msg = "Invalid HTTP request received."
                self.logger.warning(msg)
                self.send_400_response(msg)
                return

            if event is h11.NEED_DATA:
                break
            if event is h11.PAUSED:
                self.flow.pause_reading()
                break
            if isinstance(event, h11.Request):
                self.headers = [(key.lower(), value) for key, value in event.headers]
                raw_path, _, query_string = event.target.partition(b"?")
                path = unquote(raw_path.decode("ascii"))
                full_path = self.root_path + path
                full_raw_path = self.root_path.encode("ascii") + raw_path
                tls = {}
                if self._peer_certificate:
                    tls["client_cert"] = self._peer_certificate
                self.scope = {
                    "type": "http",
                    "asgi": {"version": self.asgi_version, "spec_version": "2.3"},
                    "http_version": event.http_version.decode("ascii"),
                    "server": self.server,
                    "client": self.client,
                    "scheme": self.scheme,
                    "method": event.method.decode("ascii"),
                    "root_path": self.root_path,
                    "path": full_path,
                    "raw_path": full_raw_path,
                    "query_string": query_string,
                    "headers": self.headers,
                    "state": self.app_state.copy(),
                    "extensions": {"tls": tls},
                }
                if self._should_upgrade():
                    self.handle_websocket_upgrade(event)
                    return
                if self.limit_concurrency is not None and (
                    len(self.connections) >= self.limit_concurrency
                    or len(self.tasks) >= self.limit_concurrency
                ):
                    app_for_request = service_unavailable
                else:
                    app_for_request = self.app
                self._unset_keepalive_if_required()
                self.cycle = RequestResponseCycle(
                    scope=self.scope,
                    conn=self.conn,
                    transport=self.transport,
                    flow=self.flow,
                    logger=self.logger,
                    access_logger=self.access_logger,
                    access_log=self.access_log,
                    default_headers=self.server_state.default_headers,
                    message_event=asyncio.Event(),
                    on_response=self.on_response_complete,
                )
                if self.config.reset_contextvars:
                    if sys.version_info >= (3, 11):
                        task = self.loop.create_task(
                            self.cycle.run_asgi(app_for_request),
                            context=contextvars.Context(),
                        )
                    else:
                        task = contextvars.Context().run(
                            self.loop.create_task,
                            self.cycle.run_asgi(app_for_request),
                        )
                else:
                    task = self.loop.create_task(self.cycle.run_asgi(app_for_request))
                task.add_done_callback(self.tasks.discard)
                self.tasks.add(task)
            elif isinstance(event, h11.Data):
                if self.conn.our_state is h11.DONE:
                    continue
                self.cycle.body += event.data
                if len(self.cycle.body) > HIGH_WATER_LIMIT:
                    self.flow.pause_reading()
                self.cycle.message_event.set()
            elif isinstance(event, h11.EndOfMessage):
                if self.conn.our_state is h11.DONE:
                    self.transport.resume_reading()
                    self.conn.start_next_cycle()
                    continue
                self.cycle.more_body = False
                self.cycle.message_event.set()
                if self.conn.their_state is h11.MUST_CLOSE:
                    break


def local_get_session(request: Request) -> Iterator:
    """Retain the workspace gate while avoiding PostgreSQL-only set_config."""
    workspace_id = request.scope.get("auth_workspace")
    if not workspace_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "WORKSPACE_CONTEXT_MISSING",
                "detail": "No authenticated workspace context.",
            },
        )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _write_adapter_state(adapter: object, path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "invocation_count": adapter.invocation_count,
                "invocations_by_action": dict(adapter.invocations_by_action),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


class ObservedAdapter:
    def __init__(self, adapter: object, state_path: Path) -> None:
        self._adapter = adapter
        self.actions = adapter.actions
        self.state_path = state_path
        _write_adapter_state(adapter, state_path)

    @property
    def invocation_count(self) -> int:
        return self._adapter.invocation_count

    @property
    def invocations_by_action(self) -> dict[str, int]:
        return self._adapter.invocations_by_action

    def invoke(self, action: str, resource: str, arguments: dict[str, object]) -> object:
        try:
            return self._adapter.invoke(action, resource, arguments)
        finally:
            _write_adapter_state(self._adapter, self.state_path)


def _prepare_database() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT OR IGNORE INTO merkle_leaf_sequence "
                "(id, next_value) VALUES (1, 0)"
            )
        )


def _configure_runtime() -> None:
    _prepare_database()
    app.dependency_overrides[get_session] = local_get_session
    registry = app.state.mount_registry
    adapter = registry.effect_targets.resolve("activation.local-record")
    if adapter is None:
        raise RuntimeError("activation.local-record was not registered")
    state_path = HARNESS_ROOT / "adapter_state.json"
    registry.effect_targets.register(
        "activation.local-record", ObservedAdapter(adapter, state_path)
    )


_configure_runtime()


if __name__ == "__main__":
    import os

    import uvicorn

    config = uvicorn.Config(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8443")),
        http=PeerCertificateH11Protocol,
        ssl_keyfile=str(HARNESS_ROOT / "mtls" / "server.key"),
        ssl_certfile=str(HARNESS_ROOT / "mtls" / "server.crt"),
        ssl_ca_certs=str(HARNESS_ROOT / "mtls" / "ca.crt"),
        ssl_cert_reqs=1,
        log_level="info",
    )
    uvicorn.Server(config).run()
