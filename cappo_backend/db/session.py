"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cappo_backend.config import get_settings

_settings = get_settings()
_DATABASE_TIMEOUT_SECONDS = 2

_connect_args: dict[str, object] = {}
_engine_args: dict[str, object] = {"future": True}
if _settings.database_url.startswith("sqlite"):
    _connect_args = {
        "check_same_thread": False,
        "timeout": _DATABASE_TIMEOUT_SECONDS,
    }
else:
    # Bound both initial driver connection attempts and pool acquisition so a
    # stalled database cannot accumulate unbounded health-check workers.
    _connect_args = {"connect_timeout": _DATABASE_TIMEOUT_SECONDS}
    _engine_args["pool_timeout"] = _DATABASE_TIMEOUT_SECONDS

engine = create_engine(
    _settings.database_url,
    connect_args=_connect_args,
    **_engine_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)



def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
