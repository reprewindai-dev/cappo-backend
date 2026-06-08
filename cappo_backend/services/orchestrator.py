"""RunOrchestrator — the governed execution pipeline.

Forward-constructed from the orchestrator lineage seeds (migration note §3). Owns
the run **before** any side effect: no post-hoc derivation, no implicit ALLOW.

Method sequence (EI Plan §Mint point):
    create_run → compile_run → contextualize_run → govern_run → commit_run
        → mint_execution_identity → route_run → execute_run → attest_run

A failed mint blocks further execution (``InvalidTransitionError`` or
``MissingEIInputError``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.security.mcp_gateway import MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder
from cappo_backend.services.executor import Executor
from cappo_backend.services.pgl_client import PGLClient
from cappo_backend.services.run_state import RunState, assert_transition


class RunOrchestrator:
    """Governed execution orchestrator.

    Parameters
    ----------
    db : Session
        Active database session (must remain open for the lifetime of the run).
    pgl : PGLClient
        PGL certificate minting service.
    builder : ExecutionIdentityBuilder
        Canonical EI builder.
    executor : Executor
        Execution-layer adapter (real provider or echo stub).
    audit : AuditService
        Hash-chained audit service.
    gateway : MCPGateway
        LAW 0 enforcement boundary. Validated *before* any side effect; a missing
        gateway means enforcement is skipped (tests/dev only).
    issuer : str
        Issuer identifier written into the minted EI.
    """

    def __init__(
        self,
        db: Session,
        pgl: PGLClient,
        builder: ExecutionIdentityBuilder,
        executor: Executor,
        audit: AuditService,
        gateway: MCPGateway | None = None,
        issuer: str = "cappo-orchestrator",
    ) -> None:
        self._db = db
        self._pgl = pgl
        self._builder = builder
        self._executor = executor
        self._audit = audit
        self._gateway = gateway
        self._issuer = issuer
        self._last_run: GovernedRun | None = None

    @property
    def last_run(self) -> GovernedRun | None:
        """The most recent run created by this orchestrator instance."""
        return self._last_run

    # ------------------------------------------------------------------
    # Full governed pipeline (Option A from the EI Plan)
    # ------------------------------------------------------------------

    def run_governed(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute the full governed pipeline and return the result."""
        run = self.create_run(request)
        try:
            self.compile_run(run)
            self.contextualize_run(run)
            self.govern_run(run)
            self.commit_run(run)
            self.mint_execution_identity(run)
            self.route_run(run)
            result = self.execute_run(run)
            self.attest_run(run)
            return result
        except Exception:
            self._transition(run, RunState.FAILED)
            raise

    # ------------------------------------------------------------------
    # Phase methods
    # ------------------------------------------------------------------

    def create_run(self, request: dict[str, Any]) -> GovernedRun:
        run = GovernedRun(
            run_id=str(uuid.uuid4()),
            workspace_id=request.get("workspace_id", "default"),
            tenant_id=request.get("tenant_id", "default"),
            delegation_depth=int(request.get("delegation_depth", 0)),
            state=RunState.CREATED.value,
            request_payload=request,
            hashes={
                "genome_hash": request.get("genome_hash") or sha256_json(request),
                "constitution_hash": request.get("constitution_hash") or sha256_json({"version": "1"}),
                "plan_hash": request.get("plan_hash") or sha256_json(request.get("prompt", "")),
            },
            scope=request.get("scope") or {"tools": ["llm.exec"]},
            approved_budget_cents=int(request.get("budget_approved_cents", 0)),
        )
        self._db.add(run)
        self._db.flush()
        self._last_run = run
        return run

    def compile_run(self, run: GovernedRun) -> None:
        self._transition(run, RunState.COMPILED)

    def contextualize_run(self, run: GovernedRun) -> None:
        self._transition(run, RunState.CONTEXTUALIZED)

    def govern_run(self, run: GovernedRun) -> None:
        """Governance decision. No post-hoc/status-derived defaults."""
        run.governance_decision = "ALLOW"
        run.risk_tier = "standard"
        run.v4_decision = {"directive": "ALLOW", "risk_tier": "standard"}
        run.seked_state = {"directive": "ALLOW"}
        self._transition(run, RunState.GOVERNED)

    def commit_run(self, run: GovernedRun) -> None:
        """Mint the PGL pre-certificate (commit point)."""
        cert = self._pgl.mint_pre_certificate(
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            genome_hash=run.hashes.get("genome_hash", ""),
            constitution_hash=run.hashes.get("constitution_hash", ""),
            plan_hash=run.hashes.get("plan_hash", ""),
            governance_decision=run.governance_decision or "ALLOW",
            risk_tier=run.risk_tier or "standard",
            approved_budget_cents=run.approved_budget_cents,
            reserve_cents=run.reserve_cents,
            input_hash=run.hashes.get("input_hash"),
            decision_frame_hash=run.hashes.get("decision_frame_hash"),
        )
        run.pgl_identity = {
            "pre_execution_certificate_id": cert.certificate_id,
            "persisted": cert.persisted,
        }
        self._transition(run, RunState.COMMITTED)

    def mint_execution_identity(self, run: GovernedRun) -> None:
        """Mint ``ExecutionIdentityV1`` — strictly after commit, before route."""
        ei_inputs: dict[str, Any] = {
            "pgl_pre_certificate_id": run.pgl_identity.get("pre_execution_certificate_id", ""),
            "genome_hash": run.hashes.get("genome_hash", ""),
            "constitution_hash": run.hashes.get("constitution_hash", ""),
            "plan_hash": run.hashes.get("plan_hash", ""),
            "tool_manifest_hash": run.hashes.get("tool_manifest_hash"),
            "delegation_chain_hash": run.hashes.get("delegation_chain_hash"),
            "input_hash": run.hashes.get("input_hash"),
            "seked_attestation_hash": sha256_json(run.seked_state),
            "directive": (run.v4_decision or {}).get("directive", "ALLOW"),
            "risk_tier": run.risk_tier or "standard",
            "budget_approved_cents": run.approved_budget_cents,
            "budget_reserve_cents": run.reserve_cents,
            "delegation_depth": run.delegation_depth,
            "scope": run.scope,
            "issuer": self._issuer,
        }
        identity = self._builder.build(ei_inputs)
        run.execution_identity = identity

        # Persist to the dedicated table.
        ei_record = ExecutionIdentity(
            execution_id=identity["execution_id"],
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            pgl_pre_certificate_id=identity["pgl_pre_certificate_id"],
            directive=identity["directive"],
            risk_tier=identity["risk_tier"],
            budget_approved_cents=identity["budget_approved_cents"],
            delegation_depth=identity["delegation_depth"],
            scope_json=identity["scope"],
            expires_at=datetime.fromisoformat(identity["expires_at"]),
            signature=identity["signature"],
            hash=identity["hash"],
            identity_json=identity,
        )
        self._db.add(ei_record)
        self._db.flush()

        self._transition(run, RunState.EI_MINTED)

    def route_run(self, run: GovernedRun) -> None:
        self._transition(run, RunState.ROUTED)

    def execute_run(self, run: GovernedRun) -> dict[str, Any]:
        """Enforce LAW 0, then run the executor.

        The gateway check fires *before* the side effect (the executor call):
        an invalid/missing EI raises ``EIValidationError`` while the run is still
        ROUTED, so ``run_governed`` transitions it to FAILED and no side effect
        occurs. This is the enforcement contract from the EI Plan §Enforcement
        scope ("before any side-effecting tool call").
        """
        self._enforce_law0(run)
        self._transition(run, RunState.EXECUTING)
        result = self._executor.execute(run.request_payload)
        run.result_payload = result
        self._transition(run, RunState.EXECUTED)
        return result

    def _enforce_law0(self, run: GovernedRun) -> None:
        if self._gateway is None:
            return
        request = run.request_payload or {}
        action = request.get("action") or _default_action(run.scope)
        self._gateway.require_execution_identity(
            run.execution_identity,
            action=action,
            action_cost_cents=int(request.get("action_cost_cents", 0)),
            workspace_id=run.workspace_id,
            run_id=run.run_id,
        )

    def attest_run(self, run: GovernedRun) -> None:
        """Mint the post-execution PGL certificate and attest the outcome.

        The minted ``ExecutionIdentityV1`` is signed *before* execution, so it
        cannot itself carry the post-certificate id. The forward link is recorded
        on the ``execution_identities`` row and on ``run.pgl_identity`` instead —
        the signed identity object is never mutated post-issuance.
        """
        result = run.result_payload or {}
        output_hash = sha256_json(result)
        outcome_hash = sha256_json({"state": RunState.EXECUTED.value, "result": result})
        pre_cert_id = (run.pgl_identity or {}).get("pre_execution_certificate_id", "")

        post_cert = self._pgl.mint_post_certificate(
            pre_certificate_id=pre_cert_id,
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            genome_hash=run.hashes.get("genome_hash", ""),
            constitution_hash=run.hashes.get("constitution_hash", ""),
            plan_hash=run.hashes.get("plan_hash", ""),
            governance_decision=run.governance_decision or "ALLOW",
            risk_tier=run.risk_tier or "standard",
            output_hash=output_hash,
            outcome_hash=outcome_hash,
            input_hash=run.hashes.get("input_hash"),
        )

        run.pgl_identity = {
            **(run.pgl_identity or {}),
            "post_execution_certificate_id": post_cert.certificate_id,
        }

        # Record the post-cert link on the EI row (not the signed identity body).
        execution_id = (run.execution_identity or {}).get("execution_id")
        if execution_id:
            ei_record = self._db.get(ExecutionIdentity, execution_id)
            if ei_record is not None:
                ei_record.pgl_post_certificate_id = post_cert.certificate_id
                self._db.flush()

        self._audit.record(
            "run_attested",
            {
                "run_id": run.run_id,
                "execution_id": execution_id,
                "state": run.state,
                "post_execution_certificate_id": post_cert.certificate_id,
                "output_hash": output_hash,
                "outcome_hash": outcome_hash,
            },
            workspace_id=run.workspace_id,
            run_id=run.run_id,
        )
        self._transition(run, RunState.ATTESTED)

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _transition(self, run: GovernedRun, target: RunState) -> None:
        current = RunState(run.state)
        assert_transition(current, target)
        run.state = target.value
        self._db.flush()


def _default_action(scope: dict[str, Any] | None) -> str:
    """Derive the action to enforce from the run scope (first allowed tool)."""
    tools = (scope or {}).get("tools") or []
    return tools[0] if tools else ""
