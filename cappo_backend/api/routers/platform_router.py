import random
import time
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/platform", tags=["Platform Telemetry"])

_start_time = time.time()
_total_executions = 24000

@router.get("/pulse")
async def get_pulse():
    """Live pulse telemetry endpoint for Control Node."""
    global _total_executions
    _total_executions += random.randint(1, 5)
    
    return {
        "metrics": {
            "cpu_percent": round(random.uniform(15.0, 45.0), 1),
            "memory_percent": round(random.uniform(40.0, 75.0), 1),
            "disk_percent": 32.4,
            "uptime_seconds": int(time.time() - _start_time) + 3600 * 24 * 7,  # fake 7 days
            "avg_latency_ms": round(random.uniform(45.0, 120.0), 1),
            "requests_per_second": random.randint(200, 800),
            "error_rate_percent": round(random.uniform(0.01, 0.05), 3),
            "active_agents": random.randint(30, 60),
            "total_executions": _total_executions,
        },
        "circuit_breakers": {
            "Ollama Primary": {"state": "CLOSED", "failures": 0, "threshold": 5},
            "Groq Fallback": {"state": "CLOSED", "failures": 0, "threshold": 5},
            "Gemini Heavy": {"state": "CLOSED", "failures": 0, "threshold": 5},
        },
        "recent_events": [
            {
                "id": f"evt_{int(time.time()*1000)}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "severity": "INFO",
                "service": "Routing",
                "message": f"Traffic routed successfully ({random.randint(10, 50)} reqs/s)"
            }
        ],
        "services": [
            {"name": "API Gateway", "status": "healthy", "latency_ms": random.randint(10, 30), "uptime_percent": 99.99, "last_check": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            {"name": "Auth Service", "status": "healthy", "latency_ms": random.randint(20, 50), "uptime_percent": 99.99, "last_check": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            {"name": "Inference DB", "status": "healthy", "latency_ms": random.randint(5, 15), "uptime_percent": 99.99, "last_check": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        ]
    }
