"""Benchmarks router — API Trust Rankings derived from real execution data.

Aggregates GovernedRun execution statistics by provider to produce a live
leaderboard. Falls back to seed data when no runs exist yet, but that seed
data is clearly marked and will be replaced as real runs accumulate.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.governed_run import GovernedRun

router = APIRouter(prefix="/api/v1/benchmarks", tags=["API Benchmarks"])

# Seed trust tiers for known providers (updated by real execution data)
_PROVIDER_SEED = {
    "openai": {"name": "GPT-4o", "provider": "OpenAI", "tier": "Apex", "base_score": 960},
    "groq": {"name": "Llama 3 70B (Groq)", "provider": "Groq", "tier": "Verified", "base_score": 850},
    "gemini": {"name": "Gemini 2.5 Flash", "provider": "Google", "tier": "Apex", "base_score": 975},
    "anthropic": {"name": "Claude 3.5 Sonnet", "provider": "Anthropic", "tier": "Apex", "base_score": 975},
    "ollama": {"name": "Local Ollama", "provider": "Self-hosted", "tier": "Configured", "base_score": 800},
    "echo": {"name": "Echo Stub (Dev)", "provider": "CAPPO", "tier": "Development", "base_score": 500},
    "fallback": {"name": "Fallback Provider", "provider": "Configured", "tier": "Standby", "base_score": 800},
}


def _tier_from_score(score: int) -> str:
    if score >= 950:
        return "Apex"
    if score >= 900:
        return "Sovereign"
    if score >= 800:
        return "Verified"
    if score >= 700:
        return "Standard"
    return "Development"


@router.get("/leaderboard")
async def get_leaderboard(db: Session = Depends(get_session)):
    """Live API Trust Rankings derived from real GovernedRun execution data."""
    # Aggregate per provider from real runs
    stats = (
        db.query(
            GovernedRun.result_payload,
            func.count(GovernedRun.run_id).label("run_count"),
            func.avg(
                func.cast(
                    func.json_extract(GovernedRun.result_payload, "$.latency_ms"),
                    "REAL"
                )
            ).label("avg_latency"),
        )
        .filter(GovernedRun.result_payload.isnot(None))
        .group_by(
            func.json_extract(GovernedRun.result_payload, "$.provider")
        )
        .all()
    )

    # Build provider map from real data
    real_providers: dict[str, dict] = {}
    for row in stats:
        if not isinstance(row.result_payload, dict):
            continue
        provider_key = row.result_payload.get("provider", "unknown")
        run_count = row.run_count or 0
        avg_lat = float(row.avg_latency or 0)
        seed = _PROVIDER_SEED.get(provider_key, {
            "name": provider_key.title(),
            "provider": provider_key.title(),
            "tier": "Unknown",
            "base_score": 700,
        })

        # Trust score: base score adjusted by error rate and latency
        error_run_count = (
            db.query(func.count(GovernedRun.run_id))
            .filter(
                GovernedRun.state.in_(["failed", "error", "law0_violation"]),
                func.json_extract(GovernedRun.result_payload, "$.provider") == provider_key
            )
            .scalar()
            or 0
        )
        error_rate = (error_run_count / run_count) if run_count > 0 else 0
        latency_penalty = min(50, int(avg_lat / 10))
        trust_score = max(0, int(seed["base_score"] * (1 - error_rate) - latency_penalty))

        real_providers[provider_key] = {
            "id": provider_key,
            "name": seed["name"],
            "provider": seed["provider"],
            "vabp": {
                "trust_score": trust_score,
                "tier": _tier_from_score(trust_score),
            },
            "metrics": {
                "latency_ms": round(avg_lat, 1),
                "total_runs": run_count,
                "error_rate": round(error_rate * 100, 2),
                "uptime_percent": round((1 - error_rate) * 100, 2),
            },
            "sla": {
                "staked_amount": trust_score * 50,  # proportional to trust
                "breach_probability": round(error_rate, 4),
            },
            "source": "live_db",
        }

    # Fill in seed providers not yet seen in real runs
    for key, seed in _PROVIDER_SEED.items():
        if key not in real_providers:
            real_providers[key] = {
                "id": key,
                "name": seed["name"],
                "provider": seed["provider"],
                "vabp": {
                    "trust_score": seed["base_score"],
                    "tier": seed["tier"],
                },
                "metrics": {
                    "latency_ms": 0.0,
                    "total_runs": 0,
                    "error_rate": 0.0,
                    "uptime_percent": 0.0,
                },
                "sla": {
                    "staked_amount": seed["base_score"] * 50,
                    "breach_probability": 0.0,
                },
                "source": "seed_no_runs_yet",
            }

    # Sort by trust_score descending
    sorted_apis = sorted(
        real_providers.values(),
        key=lambda x: x["vabp"]["trust_score"],
        reverse=True,
    )

    return {
        "apis": sorted_apis,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_providers": len(sorted_apis),
    }


@router.get("/staking/markets")
async def get_markets(db: Session = Depends(get_session)):
    """SLA Staking Prediction Markets — derived from real execution reliability."""
    # Pull leaderboard data from the same source
    total_runs: int = db.query(func.count(GovernedRun.run_id)).scalar() or 0
    failed_runs: int = (
        db.query(func.count(GovernedRun.run_id))
        .filter(GovernedRun.state.in_(["failed", "error", "law0_violation"]))
        .scalar()
        or 0
    )

    overall_reliability = 1 - (failed_runs / max(1, total_runs))
    odds_yes = round(min(0.999, max(0.5, overall_reliability)), 4)
    odds_no = round(1 - odds_yes, 4)

    markets = [
        {
            "id": "mkt_overall",
            "api_id": "cappo_exec",
            "label": "CAPPO Execution SLA ≥ 99.9%",
            "pool_size": max(10000, total_runs * 100),
            "odds_yes": odds_yes,
            "odds_no": odds_no,
            "total_runs": total_runs,
            "failed_runs": failed_runs,
            "source": "live_db",
        }
    ]

    return {
        "markets": markets,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
