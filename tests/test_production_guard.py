"""Tests for production PGL fallback guard (Task 6).

Production mode (CAPPO_REQUIRE_PERSISTENT_PGL=true) must fail fast if a
PGLClient is constructed without a database session. Development mode must
remain usable (non-persisted certificates are allowed).
"""

from __future__ import annotations

import pytest

from cappo_backend.config import Settings
from cappo_backend.services.pgl_client import PGLClient, PGLPersistenceError


class TestProductionGuard:
    def test_production_no_db_raises(self, prod_settings: Settings) -> None:
        with pytest.raises(PGLPersistenceError, match="forbidden"):
            PGLClient(db=None, settings=prod_settings)

    def test_production_with_db_ok(self, db, prod_settings: Settings) -> None:
        client = PGLClient(db=db, settings=prod_settings)
        assert client.persistent is True

    def test_dev_no_db_allowed(self, settings: Settings) -> None:
        client = PGLClient(db=None, settings=settings)
        assert client.persistent is False

    def test_dev_mint_non_persisted(self, settings: Settings) -> None:
        client = PGLClient(db=None, settings=settings)
        cert = client.mint_pre_certificate(
            run_id="r",
            workspace_id="ws",
            genome_hash="g",
            constitution_hash="c",
            plan_hash="p",
            governance_decision="ALLOW",
            risk_tier="standard",
        )
        assert cert.persisted is False

    def test_persistent_mint_with_db(self, db, settings: Settings) -> None:
        client = PGLClient(db=db, settings=settings)
        cert = client.mint_pre_certificate(
            run_id="r",
            workspace_id="ws",
            genome_hash="g",
            constitution_hash="c",
            plan_hash="p",
            governance_decision="ALLOW",
            risk_tier="standard",
        )
        assert cert.persisted is True
