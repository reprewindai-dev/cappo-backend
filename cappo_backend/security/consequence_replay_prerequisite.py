"""Fail-closed prerequisite for consequence replay protection.

The governed execution route validates WIT/WPT JTIs through the replay cache.
A production consequence must never silently fall back to a per-request cache
that accepts every replay when Redis is unavailable. This middleware rejects
the consequence before route execution when the production replay store is not
configured on the application.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cappo_backend.config import Settings


class ConsequenceReplayPrerequisiteMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next) -> Response:
        if (
            self._settings.is_production
            and request.method == "POST"
            and request.url.path == "/v1/exec"
            and getattr(request.app.state, "redis_client", None) is None
        ):
            return JSONResponse(
                {
                    "error": "WID_REPLAY_CACHE_UNAVAILABLE",
                    "detail": "Production governed execution requires the shared replay cache.",
                    "fail_closed": True,
                },
                status_code=503,
            )
        return await call_next(request)
