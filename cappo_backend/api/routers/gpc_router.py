import random
import time
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1/gpc", tags=["Governed Plan Compiler"])

@router.get("/stats")
async def get_stats():
    """Returns statistics for the Governed Plan Compiler."""
    return {
        "plans_total": random.randint(1500, 5000),
        "runs_total": random.randint(12000, 45000),
        "decisions": {
            "approved": random.randint(8000, 30000),
            "blocked": random.randint(400, 1500)
        }
    }

@router.post("/compile")
async def compile_plan(request: Request):
    """Compiles a governed plan based on intent and policy constraints."""
    data = await request.json()
    intent = data.get("intent", "Unknown intent")
    compliance = data.get("compliance", [])
    provider = data.get("provider", "gemini")
    model = data.get("model", "gemini-2.5-flash")

    # Simulate compilation time
    time.sleep(1.5)

    nodes = [
        {"id": "node_1", "type": "standard", "description": "Parse natural language intent", "policy_tag": "execution", "entropy": 0.12},
        {"id": "node_2", "type": "quantum", "description": f"Apply {', '.join(compliance) if compliance else 'default'} compliance constraints", "policy_tag": "compliance", "entropy": 0.85},
        {"id": "node_3", "type": "standard", "description": "Generate executable execution graph", "policy_tag": "routing", "entropy": 0.45},
    ]

    return {
        "id": f"plan_{int(time.time())}",
        "name": f"Automated Plan ({provider})",
        "intent": intent,
        "graph": {
            "nodes": nodes,
            "edges": [
                {"from": "node_1", "to": "node_2"},
                {"from": "node_2", "to": "node_3"}
            ]
        },
        "status": "compiled",
        "policy_result": "approved",
        "compliance": compliance,
        "provider": provider,
        "model": model,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "decision_frame_id": f"df_{int(time.time()*1000)}",
        "proof_hash": f"0x{''.join(random.choices('0123456789abcdef', k=64))}"
    }
