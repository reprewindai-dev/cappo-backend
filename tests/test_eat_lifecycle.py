"""End-to-end EAT lifecycle: mint EI → mint EAT → verify → consume → reject replay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from cappo_backend.security.edge_gateway import EATVerificationError, EdgeGateway
from cappo_backend.security.nonce_cache import InMemoryNonceCache
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.eat_builder import EATBuilder
from cappo_backend.services.ei_builder import Ed25519Signer, ExecutionIdentityBuilder
from cappo_backend.services.executor import EchoExecutor
from cappo_backend.services.orchestrator import RunOrchestrator
from cappo_backend.services.pgl_client import PGLClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EI_SIGNING_KEY = "test-ei-signing-key"
EAT_SIGNING_KEY = "test-eat-signing-key"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orchestrator(db, with_eat: bool = True) -> RunOrchestrator:
    """Create a full orchestrator with or without EAT builder."""
    ei_signer = Ed25519Signer(signing_key=EI_SIGNING_KEY)
    ei_builder = ExecutionIdentityBuilder(signer=ei_signer)
    pgl = PGLClient(db=db)
    executor = EchoExecutor()
    audit = AuditService(db)

    eat_builder = None
    if with_eat:
        eat_signer = Ed25519Signer(signing_key=EAT_SIGNING_KEY)
        eat_builder = EATBuilder(signer=eat_signer)

    return RunOrchestrator(
        db=db,
        pgl=pgl,
        builder=ei_builder,
        executor=executor,
        audit=audit,
        eat_builder=eat_builder,
    )


def _make_request(**overrides: Any) -> dict[str, Any]:
    """Minimal valid execution request."""
    req: dict[str, Any] = {
        "workspace_id": "ws-test",
        "tenant_id": "tenant-test",
        "prompt": "Hello, world!",
        "agent_id": "agent-lifecycle-test",
        "trust_score": 75.0,
    }
    req.update(overrides)
    return req


# ---------------------------------------------------------------------------
# Full lifecycle test
# ---------------------------------------------------------------------------


class TestEATLifecycle:
    """Full lifecycle: mint EI → mint EAT → verify → consume → reject replay."""

    def test_full_lifecycle_with_eat(self, db):
        """Orchestrator mints EAT, edge gateway verifies, and replay is blocked."""
        orch = _make_orchestrator(db, with_eat=True)
        request = _make_request()

        # Run the full governed pipeline (includes EAT minting)
        result = orch.run_governed(request)
        assert result is not None

        # The run should have an EAT
        run = orch.last_run
        assert run is not None
        assert run.eat is not None
        assert run.eat["eat_version"] == "1.0"
        assert run.eat["eat_id"].startswith("eat-")
        assert run.eat["execution_id"] == run.execution_identity["execution_id"]

        # Verify the EAT using the Edge Gateway
        nonce_cache = InMemoryNonceCache()
        audit = AuditService(db)
        gw = EdgeGateway(
            audit=audit,
            eat_signing_key=EAT_SIGNING_KEY,
            nonce_cache=nonce_cache,
        )

        # First verification should succeed
        gw.require_eat(run.eat)

        # Second verification (same nonce) should fail with replay
        with pytest.raises(EATVerificationError, match="replay") as exc_info:
            gw.require_eat(run.eat)
        assert exc_info.value.rule == "V4"

    def test_orchestrator_without_eat_builder(self, db):
        """Without an EAT builder, the run still succeeds (no EAT minted)."""
        orch = _make_orchestrator(db, with_eat=False)
        request = _make_request()

        result = orch.run_governed(request)
        assert result is not None

        run = orch.last_run
        assert run is not None
        # EAT should be None when no builder is provided
        assert run.eat is None

    def test_eat_links_to_ei(self, db):
        """The EAT's execution_id must match the EI's execution_id."""
        orch = _make_orchestrator(db, with_eat=True)
        orch.run_governed(_make_request())

        run = orch.last_run
        assert run.eat["execution_id"] == run.execution_identity["execution_id"]

    def test_eat_provenance_hashes_match_ei(self, db):
        """The EAT's provenance hashes must match the EI's hashes."""
        orch = _make_orchestrator(db, with_eat=True)
        orch.run_governed(_make_request())

        run = orch.last_run
        eat = run.eat
        ei = run.execution_identity

        assert eat["provenance"]["genome_hash"] == ei["genome_hash"]
        assert eat["provenance"]["constitution_hash"] == ei["constitution_hash"]
        assert eat["provenance"]["plan_hash"] == ei["plan_hash"]
        assert eat["provenance"]["pgl_pre_certificate_id"] == ei["pgl_pre_certificate_id"]


class TestEATExpiry:
    """EAT expiry behavior with mock clock."""

    def test_expired_eat_rejected_at_edge(self, db):
        """An EAT minted with a clock in the past should be rejected."""
        past = datetime.now(timezone.utc) - timedelta(seconds=600)
        eat_signer = Ed25519Signer(signing_key=EAT_SIGNING_KEY)
        eat_builder = EATBuilder(
            signer=eat_signer,
            _clock=lambda: past,  # mint in the past
        )

        ei_signer = Ed25519Signer(signing_key=EI_SIGNING_KEY)
        ei_builder = ExecutionIdentityBuilder(signer=ei_signer)
        pgl = PGLClient(db=db)
        executor = EchoExecutor()
        audit = AuditService(db)

        orch = RunOrchestrator(
            db=db,
            pgl=pgl,
            builder=ei_builder,
            executor=executor,
            audit=audit,
            eat_builder=eat_builder,
        )

        orch.run_governed(_make_request())
        run = orch.last_run
        assert run.eat is not None

        # The EAT was minted 600s ago with default 300s TTL → expired 300s ago
        nonce_cache = InMemoryNonceCache()
        gw = EdgeGateway(
            audit=AuditService(db),
            eat_signing_key=EAT_SIGNING_KEY,
            nonce_cache=nonce_cache,
        )

        with pytest.raises(EATVerificationError, match="expired") as exc_info:
            gw.require_eat(run.eat)
        assert exc_info.value.rule == "V3"


class TestEATRunState:
    """Verify the EAT_MINTED state is in the run lifecycle."""

    def test_run_passes_through_eat_minted_state(self, db):
        """After the full pipeline, the run must have been in EAT_MINTED state."""
        orch = _make_orchestrator(db, with_eat=True)
        orch.run_governed(_make_request())
        run = orch.last_run
        # The final state should be ATTESTED (past EAT_MINTED)
        assert run.state == "ATTESTED"
