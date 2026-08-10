"""Independently verifiable signed capability beacon endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.config import Settings
from cappo_backend.services.capability_beacon import build_beacon, published_keys, verify_beacon

router = APIRouter(prefix="/v1/capability/beacons", tags=["Capability Beacons"])
issuer_key_router = APIRouter()


class BeaconVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    beacon: dict[str, Any] = Field(min_length=1)


def get_registry(request: Request) -> MountRegistry:
    return request.app.state.mount_registry


def get_beacon_settings(request: Request) -> Settings:
    return request.app.state.settings


def cache_response(content: Any, ttl_seconds: int) -> JSONResponse:
    return JSONResponse(
        content=content,
        headers={
            "Cache-Control": f"public, max-age={max(1, ttl_seconds)}, must-revalidate",
            "Vary": "Accept-Encoding",
        },
    )


@issuer_key_router.get("/.well-known/capability-beacon-keys")
@issuer_key_router.get("/.well-known/capability-beacon-keys.json", include_in_schema=False)
def get_issuer_keys(settings: Settings = Depends(get_beacon_settings)) -> JSONResponse:
    return cache_response(
        {"issuer": settings.capability_beacon_issuer, "keys": published_keys(settings)},
        settings.capability_beacon_ttl_seconds,
    )


@router.get("")
def get_beacon_set(
    registry: MountRegistry = Depends(get_registry),
    settings: Settings = Depends(get_beacon_settings),
) -> JSONResponse:
    return cache_response(
        {"beacons": [build_beacon(package, settings) for package in registry.list_packages()]},
        settings.capability_beacon_ttl_seconds,
    )


@router.post("/verify")
def verify_presented_beacon(
    body: BeaconVerificationRequest,
    settings: Settings = Depends(get_beacon_settings),
) -> dict[str, Any]:
    valid, reason, verified_kid = verify_beacon(body.beacon, settings)
    return {"valid": valid, "reason": reason, "verified_kid": verified_kid}


@router.get("/{package_ref:path}")
def get_capability_beacon(
    package_ref: str,
    registry: MountRegistry = Depends(get_registry),
    settings: Settings = Depends(get_beacon_settings),
) -> JSONResponse:
    package = registry.packages.get(package_ref)
    if package is None:
        return JSONResponse({"error": "CAPABILITY_PACKAGE_NOT_FOUND"}, status_code=404)
    return cache_response(
        build_beacon(package, settings),
        settings.capability_beacon_ttl_seconds,
    )
