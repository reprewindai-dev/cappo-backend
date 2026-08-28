import time
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from cappo_backend.authorization.cappo_auth import CappoPreauthorizationEnforcer
from cappo_backend.identity.validator import IdentityValidator
from cappo_backend.identity.middleware import WIDMiddlewareContext, RouteClassification
from cappo_backend.identity.models import WorkloadIdentityToken, ExecutionContextToken, WorkloadProofToken, AuthorityArtifact
from cappo_backend.identity.errors import IdentityValidationError
from cappo_backend.authorization.errors import CappoAuthorizationError
from cappo_backend.pgl.evidence_validator import PGLEvidenceValidator
from cappo_backend.pgl.errors import PGLEvidenceError

router = APIRouter(tags=["wid_probe"])

class MockReplayCache:
    def check_and_store(self, jti: str, exp: int) -> bool:
        return True

replay_cache = MockReplayCache()
enforcer = CappoPreauthorizationEnforcer(replay_cache)
wid_validator = IdentityValidator("https://cappo.veklom.com", replay_cache)
wid_middleware = WIDMiddlewareContext(RouteClassification.CONSEQUENCE, wid_validator)
pgl_validator = PGLEvidenceValidator()

@router.post("/runtime/probe/cappo")
async def probe_cappo(request: Request):
    body = await request.json()
    
    # Extract tokens from body for the probe
    wit_payload = body.get("wit")
    ect_payload = body.get("ect")
    wpt_payload = body.get("wpt")
    authority_payload = body.get("authority")
    
    route = "/runtime/probe/cappo"
    method = "POST"
    trace_id = "trace-123"
    htu = "/runtime/probe/cappo"
    body_hash = "mock_body_hash"
    
    try:
        # WID Middleware Enforcement (WID-2)
        wid_middleware.enforce(
            route=route,
            method=method,
            trace_id=trace_id,
            htu=htu,
            body_hash=body_hash,
            wit_payload=wit_payload,
            ect_payload=ect_payload,
            wpt_payload=wpt_payload,
            authority_payload=authority_payload
        )
        
        # CAPPO Preauthorization (WID-3)
        if authority_payload:
            wit = WorkloadIdentityToken(**wit_payload) if wit_payload else None
            ect = ExecutionContextToken(**ect_payload) if ect_payload else None
            wpt = WorkloadProofToken(**wpt_payload) if wpt_payload else None
            auth_kwargs = dict(authority_payload)
            if "_mock_hash" in auth_kwargs:
                del auth_kwargs["_mock_hash"]
            auth = AuthorityArtifact(**auth_kwargs)
            
            enforcer.authorize_consequence(
                route=route,
                method=method,
                trace_id=trace_id,
                request_target_hash="target_hash",
                request_body_hash=body_hash,
                requested_right=body.get("requested_right", "execute"),
                wit=wit,
                ect=ect,
                wpt=wpt,
                authority=auth,
                profile_id_only=body.get("profile_id_only", False),
                api_key_only=body.get("api_key_only", False),
                expected_authority_hash=auth.authority_id,
                expected_ect_hash="ect_hash_val",
                expected_wit_hash="wit_hash_val",
                expected_scope_hash=auth.scope_hash,
                expected_policy_decision_hash=auth.policy_decision_hash
            )
            
        return {"status": "accepted"}
    except IdentityValidationError as e:
        return JSONResponse(status_code=403, content=e.to_evidence())
    except CappoAuthorizationError as e:
        return JSONResponse(status_code=403, content=e.to_evidence())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/runtime/probe/pgl")
async def probe_pgl(request: Request):
    payload = await request.json()
    try:
        # PGL Identity Chain Validation (WID-5)
        pgl_validator.validate_append(payload, is_genesis=payload.get("is_genesis", False))
        return {"status": "accepted"}
    except PGLEvidenceError as e:
        return JSONResponse(status_code=403, content=e.to_evidence())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
