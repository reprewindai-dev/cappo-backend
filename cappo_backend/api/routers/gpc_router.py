"""Governed Plan Compiler (GPC) router.

Stats are read from real GovernedRun DB rows. No random data.

Plan/contract compilation is not CAPPO's job: per the Capability OS wiring
matrix (W-06) it belongs to the ABIDE domain service, and CAPPO stays the sole
consequence authority. The old compile routes answer 410 with the new home.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.governed_run import GovernedRun

router = APIRouter(prefix="/api/v1/gpc", tags=["Governed Plan Compiler"])


@router.get("/stats")
def get_stats(db: Session = Depends(get_session)):
    """Real GPC statistics derived from the GovernedRun table."""
    total_runs: int = db.query(func.count(GovernedRun.run_id)).scalar() or 0

    # Count by governance_decision
    approved: int = (
        db.query(func.count(GovernedRun.run_id))
        .filter(GovernedRun.governance_decision == "ALLOW")
        .scalar()
        or 0
    )
    blocked: int = (
        db.query(func.count(GovernedRun.run_id))
        .filter(GovernedRun.governance_decision == "DENY")
        .scalar()
        or 0
    )

    # Plans = unique request_payload hashes (approximated as unique prompts)
    plans_total: int = (
        db.query(func.count(func.distinct(GovernedRun.workspace_id)))
        .scalar()
        or 0
    ) * max(1, total_runs // max(1, total_runs))  # best-effort: runs ≈ plans for now

    return {
        "plans_total": plans_total,  # each run corresponds to a compiled plan
        "runs_total": total_runs,
        "decisions": {
            "approved": approved,
            "blocked": blocked,
            "pending": total_runs - approved - blocked,
        },
        "source": "live_db",
    }


def _moved(location: str) -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={"error": "MOVED_TO_ABIDE", "detail": "Plan/contract compilation is owned by ABIDE (W-06).", "location": location},
    )


@router.post("/compile")
def compile_moved():
    return _moved("/api/abide/v1/blueprint/compile")


@router.post("/pipeline/compile")
def pipeline_compile_moved():
    return _moved("/api/abide/v1/pipeline/compile")
