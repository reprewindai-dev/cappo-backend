import base64
import hashlib

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RFC9530DigestMiddleware(BaseHTTPMiddleware):
    """
    Implements RFC 9530 Digest Fields for payload integrity verification.
    """
    async def dispatch(self, request: Request, call_next):
        if request.method in ["POST", "PUT", "PATCH"]:
            digest_header = request.headers.get("content-digest")
            if not digest_header:
                # Require content-digest for fedcom endpoints
                if request.url.path.startswith("/fedcom"):
                    return JSONResponse(status_code=400, content={"error": "Missing Content-Digest header"})
                return await call_next(request)

            body = await request.body()
            
            # Reconstruct the request for the next middleware since we consumed the body
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive
            
            try:
                algo, encoded_digest = digest_header.split("=", 1)
                if algo == "sha-256":
                    expected_digest = base64.b64encode(hashlib.sha256(body).digest()).decode('utf-8')
                    if encoded_digest != f":{expected_digest}:":
                        return JSONResponse(status_code=400, content={"error": "Content-Digest mismatch"})
                else:
                    return JSONResponse(status_code=400, content={"error": f"Unsupported digest algorithm: {algo}"})
            except ValueError:
                return JSONResponse(status_code=400, content={"error": "Malformed Content-Digest header"})
                
        return await call_next(request)
