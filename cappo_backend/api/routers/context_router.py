from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Dict, Any

from cappo_backend.core.governance.context_shaper import ContextShaper

router = APIRouter(prefix="/v1/context", tags=["context"])

class ShapeContextRequest(BaseModel):
    jurisdiction: str
    capability: str
    context: Dict[str, Any]

@router.post("/shape")
def shape_context(request: ShapeContextRequest):
    """
    Evaluates the capability request context against defined policies (PII filtering, 
    secret injection), shapes the context, and persists an audit event to PGL.
    """
    shaper = ContextShaper()
    result = shaper.shape_context(
        jurisdiction=request.jurisdiction,
        capability_name=request.capability,
        context=request.context
    )
    return result
