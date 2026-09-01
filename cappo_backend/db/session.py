"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from cappo_backend.config import get_settings

_settings = get_settings()
_DATABASE_TIMEOUT_SECONDS = 2

_connect_args: dict[str, object] = {}
_engine_args: dict[str, object] = {"future": True, "pool_pre_ping": True}
if _settings.database_url.startswith("sqlite"):
    _connect_args = {
        "check_same_thread": False,
        "timeout": _DATABASE_TIMEOUT_SECONDS,
    }
else:
    # Bound both initial driver connection attempts and pool acquisition so a
    # stalled database cannot accumulate unbounded health-check workers.
    _connect_args = {"connect_timeout": 10}  # 10s for local Docker
    _engine_args["pool_timeout"] = 10

engine = create_engine(
    _settings.database_url,
    connect_args=_connect_args,
    **_engine_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency that yields a tenant-scoped database session.

    ``auth_workspace`` must be present in the ASGI scope before this dependency
    yields. Absence is a typed failure (WORKSPACE_CONTEXT_MISSING, HTTP 403)
    before any DB access — RLS is defense-in-depth, not the first discovery point
    for missing authentication context.

    For routes that are genuinely workspace-agnostic, use ``get_unscoped_session``.
    """
    workspace_id: str | None = request.scope.get("auth_workspace")
    import logging
    logging.warning(f"GET_SESSION: {workspace_id} PRINCIPAL: {request.scope.get('auth_principal')}")

    if not workspace_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "WORKSPACE_CONTEXT_MISSING",
                "detail": (
                    "No authenticated workspace context. The credential must resolve "
                    "to a workspace before tenant data access begins."
                ),
            },
        )

    session = SessionLocal()
    if session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(workspace_id)},
        )
    try:
        yield session
    finally:
        session.close()


def get_unscoped_session(request: Request | None = None) -> Iterator[Session]:
    """FastAPI dependency for workspace-agnostic routes (health checks, public endpoints).

    Does NOT enforce auth_workspace. Must not be used on tenant-sensitive routes.
    """
    session = SessionLocal()
    workspace_id: str | None = None
    if request is not None:
        workspace_id = request.scope.get("auth_workspace")
    if workspace_id:
        if session.bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
                {"workspace_id": str(workspace_id)},
            )
    try:
        yield session
    finally:
        session.close()
