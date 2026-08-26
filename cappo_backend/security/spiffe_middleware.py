import hashlib
import logging

from cryptography import x509
from cryptography.x509.oid import ExtensionOID
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from cappo_backend.config import Settings

logger = logging.getLogger(__name__)


def extract_spiffe_id(cert: x509.Certificate) -> str | None:
    """Extract exactly one SPIFFE URI SAN from the certificate."""
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        spiffe_uris = []
        for name in san.value:
            if isinstance(name, x509.UniformResourceIdentifier):
                if name.value.startswith("spiffe://"):
                    spiffe_uris.append(name.value)
        if len(spiffe_uris) == 1:
            return spiffe_uris[0]
    except x509.ExtensionNotFound:
        pass
    return None


class SVIDEnforcementMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings):
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next):
        # Only enforce SPIFFE on consequence boundaries
        path = request.url.path
        if not (path.startswith("/v1/capability/mount") or path.startswith("/v1/exec")):
            return await call_next(request)

        if not self._settings.enforce_spiffe:
            return await call_next(request)

        # Do NOT trust X-Forwarded-Client-Cert. Use only direct mTLS.
        import pprint
        print("SCOPE:", pprint.pformat(request.scope))
        tls = request.scope.get("extensions", {}).get("tls", {})
        client_cert = tls.get("client_cert")

        if not client_cert:
            return JSONResponse(
                {"error": "SVID_REQUIRED", "detail": "mTLS client certificate missing"},
                status_code=403,
            )

        if isinstance(client_cert, bytes):
            try:
                cert = x509.load_pem_x509_certificate(client_cert)
                spiffe_id = extract_spiffe_id(cert)
                if not spiffe_id:
                    return JSONResponse(
                        {"error": "INVALID_SVID", "detail": "No SPIFFE URI SAN found"},
                        status_code=403,
                    )

                import datetime
                now = datetime.datetime.utcnow()
                if now < cert.not_valid_before_utc.replace(tzinfo=None) or now > cert.not_valid_after_utc.replace(tzinfo=None):
                    return JSONResponse(
                        {"error": "EXPIRED_SVID", "detail": "Certificate is expired or not yet valid"},
                        status_code=403,
                    )

                trust_domain = spiffe_id.split("/")[2] if spiffe_id.startswith("spiffe://") else ""
                if self._settings.spiffe_trust_domain and trust_domain != self._settings.spiffe_trust_domain:
                    return JSONResponse(
                        {"error": "INVALID_TRUST_DOMAIN", "detail": "Trust domain mismatch"},
                        status_code=403,
                    )

                # The user states:
                # "Is this authenticated SPIFFE workload allowed to perform this governed operation?"
                # Map SPIFFE ID -> policy
                allowed_spiffe_id = f"spiffe://{self._settings.spiffe_trust_domain}/workload/cappo-backend"
                if spiffe_id != allowed_spiffe_id:
                    return JSONResponse(
                        {"error": "SPIFFE_POLICY_MISMATCH", "detail": "Workload not authorized"},
                        status_code=403,
                    )

                # Bind to the request context
                request.scope["caller_spiffe_id"] = spiffe_id
                request.scope["trust_domain"] = trust_domain
                request.scope["caller_cert_sha256"] = hashlib.sha256(client_cert).hexdigest()
                request.scope["svid_not_before"] = cert.not_valid_before_utc.isoformat()
                request.scope["svid_not_after"] = cert.not_valid_after_utc.isoformat()

            except ValueError:
                return JSONResponse(
                    {"error": "MALFORMED_CERT", "detail": "Cannot parse client certificate"},
                    status_code=403,
                )
        else:
            # For some test environments where it might pass the object or string
            if isinstance(client_cert, str):
                try:
                    cert = x509.load_pem_x509_certificate(client_cert.encode("utf-8"))
                    spiffe_id = extract_spiffe_id(cert)
                    if not spiffe_id:
                        return JSONResponse({"error": "INVALID_SVID"}, status_code=403)
                        
                    import datetime
                    now = datetime.datetime.utcnow()
                    if now < cert.not_valid_before_utc.replace(tzinfo=None) or now > cert.not_valid_after_utc.replace(tzinfo=None):
                        return JSONResponse({"error": "EXPIRED_SVID"}, status_code=403)
                        
                    trust_domain = spiffe_id.split("/")[2] if spiffe_id.startswith("spiffe://") else ""
                    if self._settings.spiffe_trust_domain and trust_domain != self._settings.spiffe_trust_domain:
                        return JSONResponse({"error": "INVALID_TRUST_DOMAIN"}, status_code=403)
                        
                    allowed_spiffe_id = f"spiffe://{self._settings.spiffe_trust_domain}/workload/cappo-backend"
                    if spiffe_id != allowed_spiffe_id:
                        return JSONResponse({"error": "SPIFFE_POLICY_MISMATCH"}, status_code=403)
                        
                    request.scope["caller_spiffe_id"] = spiffe_id
                    request.scope["trust_domain"] = trust_domain
                    request.scope["caller_cert_sha256"] = hashlib.sha256(client_cert.encode("utf-8")).hexdigest()
                    request.scope["svid_not_before"] = cert.not_valid_before_utc.isoformat()
                    request.scope["svid_not_after"] = cert.not_valid_after_utc.isoformat()
                except ValueError:
                    return JSONResponse({"error": "MALFORMED_CERT"}, status_code=403)
            else:
                return JSONResponse({"error": "MALFORMED_CERT"}, status_code=403)

        return await call_next(request)
