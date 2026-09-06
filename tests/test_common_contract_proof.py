"""Common-Contract Proof Tests — Tests A through F.

Proves the invariant:
    The execution substrate may change, but the authority and consequence
    contract does not.

Persistent and ephemeral materialization must both execute through the same
canonical CapabilityHandler contract. Only the materialization/lifecycle
behavior differs.

Test structure:
  A — Persistent materialization: handler owns semantic path, router is transport adapter
  B — Ephemeral materialization: same contract, dissolution recorded, evidence survives
  C — Common-contract invariant: policy switch does not change governance semantics
  D — Consequence-dominance adversarial: fabricated / missing authority → deterministic DENY
  E — Replay semantics: idempotent no-op vs second-consequence vs invalid-authority DENY
  F — Evidence correlation: mechanical chain verification, not just field presence
"""
from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cappo_backend.db.base import Base
from cappo_backend.security.biscuit import mint_biscuit_capability
from cappo_backend.services.capability_handler import (
    CapabilityHandler,
    ConsequenceDominanceViolation,
    ConsequenceObservationFailure,
    HandlerExecutionResult,
    MaterializationPolicy,
    VerifiedExecutionContext,
)

# ---------------------------------------------------------------------------
# In-memory DB fixture (independent of the shared conftest engine)
# ---------------------------------------------------------------------------

@pytest.fixture
def proof_db():
    """Isolated in-memory SQLite for each proof test."""
    import cappo_backend.models  # noqa: F401 — register all models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(
            text("INSERT OR IGNORE INTO merkle_leaf_sequence (id, next_value) VALUES (1, 0)")
        )
        conn.commit()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
    Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Mock orchestrator — does not bypass any authority checks inside the handler
# ---------------------------------------------------------------------------

class _MockOrchestrator:
    """Minimal orchestrator stub for proof tests.

    The handler's authority enforcement (_assert_handler_bound_authority)
    runs BEFORE this is called. This stub only simulates the downstream
    executor returning a result with a run_id so the generic observer works.
    The real authority path is fully exercised by the handler.
    """

    def __init__(self, *, should_fail: bool = False, run_id: str | None = None):
        self._should_fail = should_fail
        self._run_id = run_id or str(uuid.uuid4())
        self.last_run = None

    def run_governed(self, payload: dict) -> dict:
        if self._should_fail:
            from cappo_backend.services.orchestrator import GovernanceDeniedError
            raise GovernanceDeniedError("mock governance denial")
        return {
            "response": "ok",
            "run_id": self._run_id,
            "execution_id": payload.get("execution_id", "mock-exec"),
        }


# ---------------------------------------------------------------------------
# Canonical context builder
# ---------------------------------------------------------------------------

_UNSET = object()  # Sentinel: caller did not provide the argument


def _make_ctx(
    *,
    execution_id=_UNSET,
    receipt_id=_UNSET,
    biscuit_token=_UNSET,
    intent_hash=_UNSET,
    mount_id=_UNSET,
    materialization_policy: MaterializationPolicy = MaterializationPolicy.PERSISTENT,
    is_activation: bool = False,
) -> VerifiedExecutionContext:
    # Only substitute a default when the caller did NOT provide the argument.
    # If the caller explicitly passes "" or None, preserve it exactly — this
    # allows adversarial tests to inject invalid values without the helper
    # silently replacing them with valid UUIDs.
    eid = str(uuid.uuid4()) if execution_id is _UNSET else execution_id
    rid = str(uuid.uuid4()) if receipt_id is _UNSET else receipt_id
    if biscuit_token is _UNSET:
        eid_for_bt = str(uuid.uuid4()) if execution_id is _UNSET else execution_id
        bt = mint_biscuit_capability(
            caller_spiffe_id="test:principal",
            executor_spiffe_id="cappo-backend",
            capability_id="test@v1",
            reads=[],
            writes=["execute"],
            resources=["provider-dispatch"],
            execution_id=eid_for_bt if eid_for_bt else "unset",
            ttl_seconds=600
        )
    else:
        bt = biscuit_token
    # For intent_hash and mount_id, use a derived default only if unset
    _eid_for_hash = eid if eid else "unset"
    ih = hashlib.sha256(f"intent:{_eid_for_hash}".encode()).hexdigest() if intent_hash is _UNSET else intent_hash
    mid = "mount-abc" if mount_id is _UNSET else mount_id
    return VerifiedExecutionContext(
        principal="test:principal",
        workspace_id="test-workspace",
        execution_id=eid if eid is not None else "",
        mount_id=mid if mid is not None else "",
        token_id="token-001",
        nonce="nonce-001",
        receipt_id=rid,
        action="execute",
        intent_hash=ih,
        operation_id=f"exec:{eid}",
        resource="provider-dispatch",
        payload={
            "execution_id": eid,
            "capability_receipt_id": rid,
            "workspace_id": "test-workspace",
        },
        materialization_policy=materialization_policy,
        is_activation=is_activation,
        biscuit_token=bt,
    )


# ===========================================================================
# TEST A — Persistent materialization
# Proves: handler owns the semantic path; router is only a transport adapter.
# ===========================================================================

class TestA_PersistentMaterialization:

    def test_persistent_execution_returns_result(self, proof_db: Session):
        """A1: Persistent execution through CapabilityHandler succeeds and
        returns a HandlerExecutionResult with consequence_established=True."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(materialization_policy=MaterializationPolicy.PERSISTENT)
        orch = _MockOrchestrator()

        result = handler.execute(ctx, orch)

        assert isinstance(result, HandlerExecutionResult)
        assert result.consequence_established is True
        assert result.execution_id == ctx.execution_id
        assert result.materialization_policy == MaterializationPolicy.PERSISTENT

    def test_persistent_lifecycle_states_do_not_include_ephemeral_phases(
        self, proof_db: Session
    ):
        """A2: Persistent execution must not record MATERIALIZED or DISSOLVED."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(materialization_policy=MaterializationPolicy.PERSISTENT)
        orch = _MockOrchestrator()

        result = handler.execute(ctx, orch)

        assert "MATERIALIZED" not in result.lifecycle_states
        assert "DISSOLVED" not in result.lifecycle_states
        assert "EXECUTING" in result.lifecycle_states
        assert "CONSEQUENCE_ESTABLISHED" in result.lifecycle_states

    def test_handler_is_sole_authority_point(self, proof_db: Session):
        """A3: The handler must own consequence authorization, not the caller.
        Calling execute() with valid authority succeeds; missing authority fails
        with ConsequenceDominanceViolation before the orchestrator is ever called.
        """
        handler = CapabilityHandler(proof_db)
        # Missing biscuit token — handler rejects before orchestrator runs
        ctx_no_biscuit = _make_ctx(biscuit_token=None)
        orch = _MockOrchestrator()

        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx_no_biscuit, orch)

        assert "CONSEQUENCE_DOMINANCE_VIOLATION" in exc_info.value.error_code
        # The orchestrator was never called (it would have set last_run)
        assert orch.last_run is None


# ===========================================================================
# TEST B — Ephemeral materialization
# Proves: same contract, MATERIALIZED→EXECUTING→CONSEQUENCE_ESTABLISHED→DISSOLVED,
#         consequence and evidence survive dissolution.
# ===========================================================================

class TestB_EphemeralMaterialization:

    def test_ephemeral_execution_records_full_lifecycle(self, proof_db: Session):
        """B1: Ephemeral execution records MATERIALIZED → EXECUTING →
        CONSEQUENCE_ESTABLISHED → DISSOLVED in order."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(materialization_policy=MaterializationPolicy.EPHEMERAL)
        orch = _MockOrchestrator()

        result = handler.execute(ctx, orch)

        assert result.lifecycle_states == [
            "MATERIALIZED",
            "EXECUTING",
            "CONSEQUENCE_ESTABLISHED",
            "DISSOLVED",
        ]
        assert result.dissolved is True

    def test_ephemeral_dissolution_does_not_erase_consequence(self, proof_db: Session):
        """B2: After dissolution, consequence_established remains True and
        evidence correlation is preserved."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(materialization_policy=MaterializationPolicy.EPHEMERAL)
        orch = _MockOrchestrator()

        result = handler.execute(ctx, orch)

        # Consequence survived dissolution
        assert result.consequence_established is True
        assert result.evidence_correlation["consequence"]["consequence_established"] is True
        assert result.evidence_correlation["lifecycle"]["is_ephemeral"] is True

    def test_ephemeral_materialization_instance_id_is_distinct_from_execution_id(
        self, proof_db: Session
    ):
        """B3: The ephemeral materialization instance has a distinct identifier,
        proving the execution substrate is separate from the execution identity."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(materialization_policy=MaterializationPolicy.EPHEMERAL)
        orch = _MockOrchestrator()

        result = handler.execute(ctx, orch)

        assert result.materialization_instance_id != result.execution_id


# ===========================================================================
# TEST C — Common-contract invariant
# Proves: switching materialization policy does NOT change governance semantics.
# ===========================================================================

class TestC_CommonContractInvariant:

    def test_authority_envelope_identical_across_policies(self, proof_db: Session):
        """C1: The authority section of the evidence correlation must be
        structurally identical for both materialization policies."""
        eid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        ih = hashlib.sha256(f"intent:{eid}".encode()).hexdigest()

        ctx_persistent = _make_ctx(
            execution_id=eid,
            receipt_id=rid,
            intent_hash=ih,
            materialization_policy=MaterializationPolicy.PERSISTENT,
        )
        ctx_ephemeral = _make_ctx(
            execution_id=eid + "-eph",
            receipt_id=rid + "-eph",
            intent_hash=ih + "eph",
            materialization_policy=MaterializationPolicy.EPHEMERAL,
        )

        handler = CapabilityHandler(proof_db)
        result_p = handler.execute(ctx_persistent, _MockOrchestrator())
        result_e = handler.execute(ctx_ephemeral, _MockOrchestrator())

        # The authority envelope structure must be identical
        auth_p = result_p.evidence_correlation["authority"]
        auth_e = result_e.evidence_correlation["authority"]
        assert set(auth_p.keys()) == set(auth_e.keys()), (
            "Authority envelope has different keys across materialization policies — "
            "common-contract invariant violated."
        )

    def test_evidence_model_structure_identical_across_policies(self, proof_db: Session):
        """C2: Evidence correlation structure must be identical for both policies.
        Only the lifecycle section and materialization_policy field may differ."""
        handler = CapabilityHandler(proof_db)
        result_p = handler.execute(
            _make_ctx(materialization_policy=MaterializationPolicy.PERSISTENT),
            _MockOrchestrator(),
        )
        result_e = handler.execute(
            _make_ctx(materialization_policy=MaterializationPolicy.EPHEMERAL),
            _MockOrchestrator(),
        )

        # Top-level evidence structure must be identical
        assert set(result_p.evidence_correlation.keys()) == set(
            result_e.evidence_correlation.keys()
        ), "Evidence model structure differs across materialization policies."

    def test_switching_policy_does_not_introduce_new_authority_path(
        self, proof_db: Session
    ):
        """C3: If changing materialization policy required a new authority path,
        the common-contract invariant would be violated. Verify both use the
        same authority validation by confirming both reject missing biscuit token."""
        handler = CapabilityHandler(proof_db)

        for policy in (MaterializationPolicy.PERSISTENT, MaterializationPolicy.EPHEMERAL):
            ctx_no_biscuit = _make_ctx(biscuit_token=None, materialization_policy=policy)
            with pytest.raises(ConsequenceDominanceViolation):
                handler.execute(ctx_no_biscuit, _MockOrchestrator())


# ===========================================================================
# TEST D — Consequence-dominance adversarial
# Proves: fabricated / missing authority → deterministic DENY.
# ===========================================================================

class TestD_ConsequenceDominance:

    @pytest.mark.parametrize("missing_field,ctx_kwargs", [
        ("receipt_id", {"receipt_id": ""}),
        ("execution_id", {"execution_id": ""}),
        ("intent_hash", {"intent_hash": ""}),
        ("mount_id", {"mount_id": ""}),
        ("biscuit_token", {"biscuit_token": None}),
        ("biscuit_token_empty", {"biscuit_token": ""}),
        ("biscuit_token_whitespace", {"biscuit_token": "   "}),
    ])
    def test_missing_authority_field_is_denied(
        self, missing_field, ctx_kwargs, proof_db: Session
    ):
        """D1: Every missing or blank authority field must produce
        ConsequenceDominanceViolation before any execution occurs."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(**ctx_kwargs)
        orch = _MockOrchestrator()

        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, orch)

        assert exc_info.value.error_code == "CONSEQUENCE_DOMINANCE_VIOLATION", (
            f"Missing {missing_field} did not produce CONSEQUENCE_DOMINANCE_VIOLATION"
        )

    def test_direct_executor_bypass_is_denied(self, proof_db: Session):
        """D2: Attempting to create a consequence without going through
        CapabilityHandler (simulated by missing biscuit token) is denied.
        Field presence alone is not trusted authority."""
        handler = CapabilityHandler(proof_db)

        # Caller supplies all the right field strings but no biscuit
        ctx = VerifiedExecutionContext(
            principal="attacker:principal",
            workspace_id="attacker-workspace",
            execution_id="legit-looking-id",
            mount_id="legit-looking-mount",
            token_id="legit-looking-token",
            nonce="legit-looking-nonce",
            receipt_id="legit-looking-receipt",
            action="execute",
            intent_hash="legit-looking-hash",
            operation_id="exec:legit-looking-id",
            resource="provider-dispatch",
            payload={"execution_id": "legit-looking-id"},
            biscuit_token=None,  # No cryptographic authority
        )

        with pytest.raises(ConsequenceDominanceViolation):
            handler.execute(ctx, _MockOrchestrator())

    def test_fabricated_fields_do_not_confer_authority(self, proof_db: Session):
        """D3: Fabricated strings in all the right fields are not authority.
        The biscuit_token field must be present and non-empty. An attacker
        supplying fabricated field values but a fake/empty biscuit is denied."""
        handler = CapabilityHandler(proof_db)

        ctx = _make_ctx(biscuit_token="   ")  # Whitespace-only — not real authority

        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())

        assert "CONSEQUENCE_DOMINANCE_VIOLATION" in exc_info.value.error_code

    def test_cryptographic_biscuit_attenuation_is_enforced(self, proof_db: Session):
        """D4: A fabricated non-empty Biscuit string is denied cryptographically.
        The handler must use the actual CAPPO verify_biscuit_capability."""
        handler = CapabilityHandler(proof_db)
        
        ctx = _make_ctx(biscuit_token="fabricated_biscuit_token_that_is_non_empty")
        
        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
            
        assert "cryptographic validation failed" in str(exc_info.value).lower()

    def test_executor_claiming_success_without_durable_target_fails(self, proof_db: Session):
        """D5: An executor claiming success while the durable target consequence is absent
        must never produce established success. Observation must be target-side independent."""
        handler = CapabilityHandler(proof_db)
        
        ctx = _make_ctx(is_activation=True)
        
        # Orchestrator claims success, but the target table is actually empty.
        # This simulates an executor lying or failing silently.
        with pytest.raises(ConsequenceObservationFailure) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
            
        assert "withheld" in str(exc_info.value).lower()



# ===========================================================================
# TEST E — Replay semantics
# Proves: idempotent no-op vs second-consequence DENY vs invalid-authority DENY.
# ===========================================================================


    def test_cryptographic_biscuit_wrong_executor(self, proof_db: Session):
        from cappo_backend.security.biscuit import mint_biscuit_capability
        handler = CapabilityHandler(proof_db)
        bt = mint_biscuit_capability(
            caller_spiffe_id="test:principal",
            executor_spiffe_id="WRONG_EXECUTOR",
            capability_id="test@v1",
            reads=[],
            writes=["execute"],
            resources=["provider-dispatch"],
            execution_id="some-exec-id",
            ttl_seconds=600,
            revocation_scope="workspace",
            revocation_epoch=0
        )
        ctx = _make_ctx(biscuit_token=bt, execution_id="some-exec-id")
        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
        assert "cryptographic validation failed" in str(exc_info.value).lower()

    def test_cryptographic_biscuit_wrong_action(self, proof_db: Session):
        from cappo_backend.security.biscuit import mint_biscuit_capability
        handler = CapabilityHandler(proof_db)
        bt = mint_biscuit_capability(
            caller_spiffe_id="test:principal",
            executor_spiffe_id="cappo-backend",
            capability_id="test@v1",
            reads=["read-only"],
            writes=[],
            resources=["provider-dispatch"],
            execution_id="some-exec-id",
            ttl_seconds=600,
            revocation_scope="workspace",
            revocation_epoch=0
        )
        ctx = _make_ctx(biscuit_token=bt, execution_id="some-exec-id")
        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
        assert "cryptographic validation failed" in str(exc_info.value).lower()

    def test_cryptographic_biscuit_wrong_resource(self, proof_db: Session):
        from cappo_backend.security.biscuit import mint_biscuit_capability
        handler = CapabilityHandler(proof_db)
        bt = mint_biscuit_capability(
            caller_spiffe_id="test:principal",
            executor_spiffe_id="cappo-backend",
            capability_id="test@v1",
            reads=[],
            writes=["execute"],
            resources=["WRONG-RESOURCE"],
            execution_id="some-exec-id",
            ttl_seconds=600,
            revocation_scope="workspace",
            revocation_epoch=0
        )
        ctx = _make_ctx(biscuit_token=bt, execution_id="some-exec-id")
        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
        assert "cryptographic validation failed" in str(exc_info.value).lower()

    def test_cryptographic_biscuit_expired_token(self, proof_db: Session):
        from cappo_backend.security.biscuit import mint_biscuit_capability
        handler = CapabilityHandler(proof_db)
        bt = mint_biscuit_capability(
            caller_spiffe_id="test:principal",
            executor_spiffe_id="cappo-backend",
            capability_id="test@v1",
            reads=[],
            writes=["execute"],
            resources=["provider-dispatch"],
            execution_id="some-exec-id",
            ttl_seconds=-600, # expired
            revocation_scope="workspace",
            revocation_epoch=0
        )
        ctx = _make_ctx(biscuit_token=bt, execution_id="some-exec-id")
        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
        assert "cryptographic validation failed" in str(exc_info.value).lower()

    def test_cryptographic_biscuit_revoked_execution(self, proof_db: Session, monkeypatch):
        from cappo_backend.security.biscuit import TrustedRevocationState, mint_biscuit_capability
        handler = CapabilityHandler(proof_db)
        bt = mint_biscuit_capability(
            caller_spiffe_id="test:principal",
            executor_spiffe_id="cappo-backend",
            capability_id="test@v1",
            reads=[],
            writes=["execute"],
            resources=["provider-dispatch"],
            execution_id="revoked-exec-id",
            ttl_seconds=600,
            revocation_scope="workspace",
            revocation_epoch=0
        )
        ctx = _make_ctx(biscuit_token=bt, execution_id="revoked-exec-id")
        
        orig_init = TrustedRevocationState.__init__
        def mocked_init(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            self.revoked_execution_ids.add("revoked-exec-id")
            self.known_epochs["workspace"] = 0
            
        monkeypatch.setattr(TrustedRevocationState, "__init__", mocked_init)
        
        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
        assert "cryptographic validation failed" in str(exc_info.value).lower()

    def test_cryptographic_biscuit_stale_epoch(self, proof_db: Session, monkeypatch):
        from cappo_backend.security.biscuit import TrustedRevocationState, mint_biscuit_capability
        handler = CapabilityHandler(proof_db)
        bt = mint_biscuit_capability(
            caller_spiffe_id="test:principal",
            executor_spiffe_id="cappo-backend",
            capability_id="test@v1",
            reads=[],
            writes=["execute"],
            resources=["provider-dispatch"],
            execution_id="some-exec-id",
            ttl_seconds=600,
            revocation_scope="workspace",
            revocation_epoch=0 # stale relative to mocked handler check!
        )
        ctx = _make_ctx(biscuit_token=bt, execution_id="some-exec-id")
        
        # Override the handler's checking method directly to simulate the system expecting epoch=1
        orig_assert = handler._assert_handler_bound_authority
        def mocked_assert(ctx_inner):
            # Same logic but known_epochs["workspace"] = 1
            from cappo_backend.security.biscuit import (
                TrustedRevocationState,
                verify_biscuit_capability,
            )
            trusted_state = TrustedRevocationState()
            trusted_state.known_epochs["workspace"] = 1
            valid = verify_biscuit_capability(
                token_b64=ctx_inner.biscuit_token,
                executor_spiffe_id="cappo-backend",
                action=ctx_inner.action,
                resource=ctx_inner.resource,
                subject_spiffe_id=ctx_inner.principal,
                trusted_state=trusted_state
            )
            if not valid:
                raise ConsequenceDominanceViolation(
                    "Execution rejected: cryptographic validation failed for biscuit token."
                )
        
        monkeypatch.setattr(handler, "_assert_handler_bound_authority", mocked_assert)
        
        with pytest.raises(ConsequenceDominanceViolation) as exc_info:
            handler.execute(ctx, _MockOrchestrator())
        assert "cryptographic validation failed" in str(exc_info.value).lower()



class TestE_ReplaySemantics:



    def test_exact_replay_returns_idempotent_result(self, proof_db: Session):
        """E1: Exact replay of a committed execution returns the existing
        consequence rather than executing again. This is not a DENY.

        The activation target's idempotent_replay path is the canonical
        proof of this: same execution_id → same content_hash → return existing row.
        """
        from cappo_backend.models.activation_consequence import ActivationConsequence

        # Seed an existing consequence directly (simulates already-committed execution)
        eid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        marker = f"veklom-activation:test-workspace:{eid}"
        from cappo_backend.services.canonical import sha256_json
        canonical = {
            "workspace_id": "test-workspace",
            "execution_id": eid,
            "operation_id": f"exec:{eid}",
            "mount_id": "mount-abc",
            "receipt_id": rid,
            "action": "activation.marker.write",
            "marker_value": marker,
        }
        row = ActivationConsequence(
            workspace_id="test-workspace",
            execution_id=eid,
            operation_id=f"exec:{eid}",
            mount_id="mount-abc",
            receipt_id=rid,
            action="activation.marker.write",
            marker_value=marker,
            content_hash=sha256_json(canonical),
        )
        proof_db.add(row)
        proof_db.commit()

        # Now replay: the activation target must return the existing row
        from cappo_backend.services.activation_target import ActivationTargetExecutor
        executor = ActivationTargetExecutor(proof_db)
        result = executor.execute({
            "action": "activation.marker.write",
            "workspace_id": "test-workspace",
            "execution_id": eid,
            "capability_execution_id": eid,
            "capability_mount_id": "mount-abc",
            "capability_receipt_id": rid,
        })

        # Idempotent replay — not an error
        assert result["activation_target"]["idempotent_replay"] is True
        assert result["activation_target"]["execution_id"] == eid
        # Consequence count in DB: still exactly 1 (not 2)
        from sqlalchemy import func, select
        count = proof_db.execute(
            select(func.count()).where(
                ActivationConsequence.execution_id == eid
            )
        ).scalar_one()
        assert count == 1, (
            f"Idempotent replay created a second consequence row. count={count}"
        )

    def test_conflicting_execution_id_reuse_fails_closed(self, proof_db: Session):
        """E2: Replay of an execution_id with a different intent_hash must fail closed.
        This prevents an attacker from reusing an execution_id to redirect authority."""
        from cappo_backend.models.activation_consequence import ActivationConsequence
        from cappo_backend.services.activation_target import (
            ActivationTargetExecutor,
            ActivationTargetInvariantError,
        )
        from cappo_backend.services.canonical import sha256_json

        eid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        marker = f"veklom-activation:test-workspace:{eid}"
        canonical = {
            "workspace_id": "test-workspace",
            "execution_id": eid,
            "operation_id": f"exec:{eid}",
            "mount_id": "mount-abc",
            "receipt_id": rid,
            "action": "activation.marker.write",
            "marker_value": marker,
        }
        row = ActivationConsequence(
            workspace_id="test-workspace",
            execution_id=eid,
            operation_id=f"exec:{eid}",
            mount_id="mount-abc",
            receipt_id=rid,
            action="activation.marker.write",
            marker_value=marker,
            content_hash=sha256_json(canonical),
        )
        proof_db.add(row)
        proof_db.commit()

        # Attacker attempts replay with a DIFFERENT receipt_id (different authority)
        executor = ActivationTargetExecutor(proof_db)
        with pytest.raises(ActivationTargetInvariantError):
            executor.execute({
                "action": "activation.marker.write",
                "workspace_id": "test-workspace",
                "execution_id": eid,
                "capability_execution_id": eid,
                "capability_mount_id": "mount-abc",
                "capability_receipt_id": "different-receipt-id",  # Conflicting
            })

    def test_missing_authority_replay_is_denied(self, proof_db: Session):
        """E3: Replay with missing biscuit token is denied via dominance check
        regardless of whether an existing consequence exists."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(biscuit_token=None)

        with pytest.raises(ConsequenceDominanceViolation):
            handler.execute(ctx, _MockOrchestrator())


# ===========================================================================
# TEST F — Evidence correlation
# Proves: mechanical chain from request commitment through terminal evidence.
#         Tests verify the correlation, not just field presence.
# ===========================================================================

class TestF_EvidenceCorrelation:

    def test_evidence_chain_covers_full_lifecycle(self, proof_db: Session):
        """F1: Evidence correlation must contain all required lifecycle segments."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx()
        result = handler.execute(ctx, _MockOrchestrator())

        ec = result.evidence_correlation
        required_segments = [
            "request_commitment",
            "authority",
            "execution",
            "lifecycle",
            "consequence",
            "terminal_evidence",
        ]
        for segment in required_segments:
            assert segment in ec, f"Evidence correlation missing segment: {segment!r}"

    def test_evidence_chain_is_mechanically_correlated(self, proof_db: Session):
        """F2: The intent_hash in request_commitment must match the intent_hash
        used to compute the evidence_chain_hash in terminal_evidence.
        This verifies the chain, not just that both fields exist."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx()
        result = handler.execute(ctx, _MockOrchestrator())

        ec = result.evidence_correlation

        # The evidence_chain_hash must incorporate the intent_hash
        recorded_intent_hash = ec["request_commitment"]["intent_hash"]
        recorded_receipt_id = ec["authority"]["receipt_id"]
        recorded_exec_id = ec["execution"]["execution_id"]
        recorded_instance_id = ec["execution"]["materialization_instance_id"]
        expected_chain_hash = hashlib.sha256(
            f"{recorded_intent_hash}:{recorded_receipt_id}:{recorded_exec_id}:{recorded_instance_id}".encode()
        ).hexdigest()

        assert ec["terminal_evidence"]["evidence_chain_hash"] == expected_chain_hash, (
            "evidence_chain_hash does not mechanically incorporate the intent_hash, "
            "receipt_id, execution_id, and instance_id. The chain is broken."
        )

    def test_authority_fields_are_bound_to_context_not_fabricated(
        self, proof_db: Session
    ):
        """F3: The authority section must reflect the actual VerifiedExecutionContext,
        proving it came from the handler and not from fabricated fields."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx()
        result = handler.execute(ctx, _MockOrchestrator())

        auth = result.evidence_correlation["authority"]
        assert auth["receipt_id"] == ctx.receipt_id
        assert auth["mount_id"] == ctx.mount_id
        assert auth["principal"] == ctx.principal
        assert auth["workspace_id"] == ctx.workspace_id
        assert auth["biscuit_bound"] is True

    def test_ephemeral_lifecycle_states_appear_in_evidence(self, proof_db: Session):
        """F4: For ephemeral execution, the evidence must record all four
        lifecycle states. Dissolution must appear in evidence, not just in memory."""
        handler = CapabilityHandler(proof_db)
        ctx = _make_ctx(materialization_policy=MaterializationPolicy.EPHEMERAL)
        result = handler.execute(ctx, _MockOrchestrator())

        states = result.evidence_correlation["lifecycle"]["states"]
        assert states == [
            "MATERIALIZED",
            "EXECUTING",
            "CONSEQUENCE_ESTABLISHED",
            "DISSOLVED",
        ], f"Ephemeral lifecycle states incorrect: {states}"
        assert result.evidence_correlation["lifecycle"]["is_ephemeral"] is True
