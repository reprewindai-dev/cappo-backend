from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["protocol"])

@router.get("/protocol.json")
def get_protocol() -> Dict[str, Any]:
    return {
        "service": "cappo-backend",
        "repo": "reprewindai-dev/cappo-backend",
        "role": "governed-runtime-execution",
        "version": "2026.07",
        "base_url": "https://capi.veklom.com",
        "health": "/health",
        "dependencies": "/health/dependencies",
        "auth_mode": "bearer",
        "capabilities": ["cAPI-execution", "runtime-guard"],
        "links": {
            "cappo": "https://capi.veklom.com/protocol.json",
            "ledger": "https://ledger.veklom.com/protocol.json",
            "interlink": "https://interlink.veklom.com/protocol.json",
            "core": "https://api.veklom.com/protocol.json"
        },
        "status": "ok"
    }

@router.post("/protocol/introspect")
def introspect_protocol(payload: dict) -> dict:
    return {
        "status": "ok",
        "matched_capabilities": ["cAPI-execution", "runtime-guard"],
        "routing_info": {"base_url": "https://capi.veklom.com"}
    }

@router.get("/health")
def healthcheck() -> dict:
    return {"status": "ok"}

@router.get("/health/dependencies")
async def health_dependencies() -> dict:
    return {
        "status": "degraded",
        "reason": "dependency health endpoint not fully wired yet"
    }
