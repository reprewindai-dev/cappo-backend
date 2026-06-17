import random
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/benchmarks", tags=["API Benchmarks"])

@router.get("/leaderboard")
async def get_leaderboard():
    """Returns the API Trust Rankings and Benchmark Leaderboard."""
    apis = [
        {
            "id": "gemini-1.5-pro",
            "name": "Gemini 1.5 Pro",
            "provider": "Google",
            "vabp": {"trust_score": 985, "tier": "Apex"},
            "metrics": {"latency_ms": 110, "uptime_percent": 99.99},
            "sla": {"staked_amount": 50000, "breach_probability": 0.001}
        },
        {
            "id": "gpt-4o",
            "name": "GPT-4o",
            "provider": "OpenAI",
            "vabp": {"trust_score": 960, "tier": "Apex"},
            "metrics": {"latency_ms": 140, "uptime_percent": 99.95},
            "sla": {"staked_amount": 45000, "breach_probability": 0.002}
        },
        {
            "id": "claude-3.5-sonnet",
            "name": "Claude 3.5 Sonnet",
            "provider": "Anthropic",
            "vabp": {"trust_score": 975, "tier": "Apex"},
            "metrics": {"latency_ms": 125, "uptime_percent": 99.98},
            "sla": {"staked_amount": 48000, "breach_probability": 0.0015}
        },
        {
            "id": "llama-3-70b-groq",
            "name": "Llama 3 70B",
            "provider": "Groq",
            "vabp": {"trust_score": 850, "tier": "Verified"},
            "metrics": {"latency_ms": 15, "uptime_percent": 99.9},
            "sla": {"staked_amount": 10000, "breach_probability": 0.01}
        }
    ]
    return {"apis": apis}

@router.get("/staking/markets")
async def get_markets():
    """Returns SLA Staking Prediction Markets."""
    markets = [
        {
            "id": "mkt_1",
            "api_id": "gemini-1.5-pro",
            "pool_size": 250000,
            "odds_yes": 0.99,
            "odds_no": 0.01
        },
        {
            "id": "mkt_2",
            "api_id": "gpt-4o",
            "pool_size": 180000,
            "odds_yes": 0.98,
            "odds_no": 0.02
        }
    ]
    return {"markets": markets}
