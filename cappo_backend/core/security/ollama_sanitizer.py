import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class OllamaBleedSanitizerMiddleware(BaseHTTPMiddleware):
    """Attach a fail-safe Ollama lifecycle policy signal to inference requests.

    This middleware is not the provider enforcement boundary and does not claim
    that model memory was flushed. Actual ``keep_alive=0`` enforcement belongs
    in the native Ollama request builder. The legacy class name is retained to
    avoid an import-breaking change while callers migrate to the clearer policy
    semantics.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        inference_route = path.startswith("/api/v1/inference") or path.startswith(
            "/v1/chat/completions"
        )

        if inference_route:
            try:
                headers = dict(request.scope["headers"])
                headers[b"x-veklom-require-ollama-keep-alive-zero"] = b"true"
                request.scope["headers"] = list(headers.items())
            except Exception:
                logger.exception("[OllamaPolicy] Failed to attach lifecycle policy signal")
                return JSONResponse(
                    status_code=500,
                    content={"error": "Ollama lifecycle policy signal failed."},
                )

        # No client-visible "sanitized" or "verified" success header is emitted.
        # Only the provider adapter can establish what was actually transmitted.
        return await call_next(request)
