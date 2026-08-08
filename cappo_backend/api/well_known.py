from fastapi import APIRouter
from cappo_backend.core.governance.context_shaper import ContextShaper

router = APIRouter()

@router.get("/.well-known/capabilities.json")
def get_capabilities():
    """
    Public discovery endpoint for the Trust Spine.
    Exposes the capability contracts currently governed by CAPPO.
    """
    shaper = ContextShaper()
    return shaper.capability_contracts
