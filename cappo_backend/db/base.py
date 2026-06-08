"""SQLAlchemy declarative base and a portable JSON column type.

The migration note observed that the old backend stored structured proof payloads
as opaque ``Text`` blobs in some places while ``AIAuditLog`` used the native
``JSON`` type. CAPPO standardises on queryable JSON for all proof/identity
payloads via SQLAlchemy's ``JSON`` type (which maps to JSONB on Postgres and a
JSON-encoded TEXT on SQLite).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
