"""FastAPI Router for PGL Genomes (/v1/genomes)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cappo_backend.adapters.local import (
    DirectQueueAdapter,
    InMemoryCacheAdapter,
    SQLiteGraphAdapter,
    SQLiteStoreAdapter,
)
from cappo_backend.db.session import get_session
from cappo_backend.services.genome_service import GenomeService

router = APIRouter(prefix="/v1/genomes", tags=["Genomes"])

# We instantiate global in-memory cache and queue to persist state across requests
_global_cache = InMemoryCacheAdapter()
_global_queue = DirectQueueAdapter()


def get_genome_service(db: Session = Depends(get_session)) -> GenomeService:
    store = SQLiteStoreAdapter(db)
    graph = SQLiteGraphAdapter(db)
    return GenomeService(
        store=store,
        graph=graph,
        cache=_global_cache,
        queue=_global_queue,
    )


# ---------- schemas ----------


class GenomeLayerData(BaseModel):
    model_layer: dict[str, Any] = Field(default_factory=dict)
    prompt_layer: dict[str, Any] = Field(default_factory=dict)
    policy_layer: dict[str, Any] = Field(default_factory=dict)
    watchtower_layer: dict[str, Any] = Field(default_factory=dict)
    task_profile: dict[str, Any] = Field(default_factory=dict)


class GenomeRegisterRequest(BaseModel):
    model_layer: dict[str, Any]
    prompt_layer: dict[str, Any]
    policy_layer: dict[str, Any]
    watchtower_layer: dict[str, Any]
    task_profile: dict[str, Any]
    parent_genome_hash: str | None = None
    relationship_type: str = "DERIVED_FROM"


class GenomeRegisterResponse(BaseModel):
    genome_hash: str
    birth_certificate: dict[str, Any]


class GenomeDiffRequest(BaseModel):
    hash_a: str
    hash_b: str


# ---------- routes ----------


@router.post("", response_model=GenomeRegisterResponse)
def register_genome(
    body: GenomeRegisterRequest,
    db: Session = Depends(get_session),
    service: GenomeService = Depends(get_genome_service),
) -> Any:
    """Register a new genome, construct the Merkle root, and persist."""
    try:
        result = service.register_genome(
            model_layer=body.model_layer,
            prompt_layer=body.prompt_layer,
            policy_layer=body.policy_layer,
            watchtower_layer=body.watchtower_layer,
            task_profile=body.task_profile,
            parent_genome_hash=body.parent_genome_hash,
            relationship_type=body.relationship_type,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/diff", response_model=list[dict[str, Any]])
def diff_genomes(
    body: GenomeDiffRequest,
    service: GenomeService = Depends(get_genome_service),
) -> Any:
    """Compute an RFC 6902-style diff between two genomes."""
    try:
        return service.diff_genomes(body.hash_a, body.hash_b)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=list[dict[str, Any]])
def list_genomes(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    service: GenomeService = Depends(get_genome_service),
) -> Any:
    """List registered genomes (metadata summary only)."""
    return service.list_genomes(offset=offset, limit=limit)


@router.get("/{genome_hash}", response_model=dict[str, Any])
def get_genome(
    genome_hash: str,
    service: GenomeService = Depends(get_genome_service),
) -> Any:
    """Fetch genome detailed configuration layers by hash."""
    genome = service.get_genome(genome_hash)
    if not genome:
        raise HTTPException(status_code=404, detail=f"Genome not found: {genome_hash}")
    return genome


@router.get("/{genome_hash}/lineage", response_model=dict[str, Any])
def get_lineage(
    genome_hash: str,
    service: GenomeService = Depends(get_genome_service),
) -> Any:
    """Fetch ancestor and descendant lineage trees for a genome hash."""
    return service.get_lineage(genome_hash)


@router.get("/{genome_hash}/birth-certificate", response_model=dict[str, Any])
def get_birth_certificate(
    genome_hash: str,
    service: GenomeService = Depends(get_genome_service),
) -> Any:
    """Fetch/mint birth certificate for a genome hash."""
    try:
        return service.mint_birth_certificate(genome_hash)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
