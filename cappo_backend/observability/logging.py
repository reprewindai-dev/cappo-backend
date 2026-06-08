"""Structured (JSON) logging configuration.

EI Plan / GAP "observability": the old backend logged to plain stdout. CAPPO
emits one JSON object per log record so a deployment can ship governance events
(especially ``cappo.law0`` alerts) straight into a structured log pipeline.

Stdlib-only (no extra dependency). ``configure_logging`` is idempotent and is
called once at application startup.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging

# Standard ``LogRecord`` attributes we do not want to duplicate inside "extra".
_RESERVED = frozenset(
    vars(logging.makeLogRecord({})).keys()
) | {"message", "asctime", "taskName"}


class JsonLogFormatter(logging.Formatter):
    """Render a ``LogRecord`` as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        # Promote any structured ``extra=...`` fields (e.g. the LAW 0 alert
        # context) to top-level keys, skipping stdlib-reserved attributes.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: int | str = logging.INFO) -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level)

    handler: logging.Handler
    if root.handlers:
        handler = root.handlers[0]
    else:
        handler = logging.StreamHandler()
        root.addHandler(handler)
    handler.setFormatter(JsonLogFormatter())
