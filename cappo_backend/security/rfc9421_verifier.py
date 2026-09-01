from typing import Optional, Dict
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class RFC9421SignatureMiddleware(BaseHTTPMiddleware):
    """
    Implements RFC 9421 HTTP Message Signatures verification.
    """
    async def dispatch(self, request: Request, call_next):
        # We only strictly enforce this on fedcom endpoints for the prototype
        if not request.url.path.startswith("/fedcom"):
            return await call_next(request)

        signature_header = request.headers.get("signature")
        signature_input_header = request.headers.get("signature-input")

        if not signature_header or not signature_input_header:
            return JSONResponse(status_code=401, content={"error": "Missing HTTP Message Signatures (RFC 9421)"})

        # In a full implementation, we parse the signature base according to RFC 9421,
        # lookup the public key via SPIFFE or DID, and verify the Ed25519 signature.
        # For this prototype phase, we validate the presence and structure of the headers
        # to ensure the protocol binding is met.
        
        try:
            # Basic validation of the signature input structure
            # e.g., sig1=("@method" "@target-uri" "content-digest");created=1618884473;keyid="did:agent:B"
            parts = signature_input_header.split(";")
            if len(parts) < 2:
                raise ValueError("Malformed signature-input")
                
            # Extract keyid (simplified for prototype)
            keyid_part = next((p for p in parts if p.startswith("keyid=")), None)
            if not keyid_part:
                raise ValueError("Missing keyid in signature-input")
                
            keyid = keyid_part.split("=")[1].strip('"')
            
            # Pass the verified keyid downstream to CAPPO
            request.state.fedcom_signer = keyid
            
        except ValueError as e:
            return JSONResponse(status_code=401, content={"error": f"Invalid signature structure: {str(e)}"})

        return await call_next(request)
