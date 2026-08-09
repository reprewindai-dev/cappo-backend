"""RunOrchestrator - the governed execution pipeline.

Forward-constructed from the orchestrator lineage seeds (migration note -3). Owns
the run **before** any side effect: no post-hoc derivation, no implicit ALLOW.

Method sequence (EI Plan -Mint point):
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
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.eat_builder import EATBuilder
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder
from cappo_backend.services.executor import Executor
from cappo_backend.services.pgl_client import PGLClient, PostCertificateParams, PreCertificateParams
from cappo_backend.services.run_state import RunState, assert_transition


class MissingGovernanceDecisionError(RuntimeError):
    """Raised when a run reaches governance without an explicit decision."""


class GovernanceDeniedError(RuntimeError):
    """Raised when CAPPO explicitly vetoes a governed run."""


_ALLOW_DIRECTIVES = {"ALLOW", "ALLOW_WITH_AUDIT"}
_DENY_DIRECTIVES = {"DENY", "DENIED"}
_VALID_DIRECTIVES = _ALLOW_DIRECTIVES | _DENY_DIRECTIVES


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
    audit : AuditService
        Hash-chained audit service.
    eat_builder : EATBuilder | None
        EAT builder for minting Execution Authorization Tokens. When present, the
        orchestrator mints an EAT after the EI and stores it on the run.
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
        eat_builder: EATBuilder | None = None,
        issuer: str = "cappo-orchestrator",
        genome_service: Any | None = None,
        gateway: Any | None = None,
    ) -> None:
        self._db = db
        self._pgl = pgl
        self._builder = builder
        self._executor = executor
        self._audit = audit
        self._eat_builder = eat_builder
        self._issuer = issuer
        self._genome_service = genome_service
        self._gateway = gateway
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
            self.mint_eat(run)
            self.route_run(run)
            self.validate_with_capi(run)
            result = self.execute_run(run)
            self.attest_run(run)
            return result
        except Exception as exc:
            self._transition(run, RunState.FAILED)
            # Record the failure in the audit log (audit all rejections/failures).
            self._audit.record(
                "run_failed",
                {
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                    "state_at_failure": run.state,
                },
                workspace_id=run.workspace_id,
                run_id=run.run_id,
            )
            raise

    def validate_with_capi(self, run: GovernedRun) -> None:
        """Query the central cAPI execution endpoint to validate the run."""
        import httpx

        from cappo_backend.config import get_settings

        settings = get_settings()

        if not settings.capi_external_validation_enabled:
            return

        base_url = settings.veklom_byos_backend_url
        if not base_url:
            return

        normalized_url = base_url.rstrip("/")
        if normalized_url.endswith("/v1"):
            capi_url = normalized_url[:-3] + "/api/v1/capi/execute"
        else:
            capi_url = normalized_url + "/api/v1/capi/execute"

        request_payload = run.request_payload or {}
        agent_id = request_payload.get("agent_id") or "agent_cappo"
        pgl_id = request_payload.get("pgl_id") or "pgl_cappo_default_sig"

        # Build cAPI execution intent
        intent = {
            "agent_id": agent_id,
            "pgl_id": pgl_id,
            "mission_id": run.run_id,
            "target_protocol": "http",
            "action": request_payload.get("action") or "cappo.exec",
            "payload": request_payload,
        }

        headers = {}
        if settings.veklom_api_key:
            headers["Authorization"] = f"Bearer {settings.veklom_api_key}"

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(capi_url, json=intent, headers=headers)

            if response.status_code != 200:
                from fastapi import HTTPException

                detail_data = {
                    "error": "cAPI_VETO_ENGAGED",
                    "message": "Execution intent violated cAPI validation rules.",
                }
                try:
                    res_json = response.json()
                    if "detail" in res_json:
                        detail_data = res_json["detail"]
                    elif "message" in res_json:
                        detail_data["message"] = res_json["message"]
                except Exception:
                    pass
                raise HTTPException(status_code=403, detail=detail_data)
        except HTTPException:
            raise
        except Exception as e:
            from fastapi import HTTPException

            raise HTTPException(status_code=502, detail=f"cAPI Gateway connection failed: {str(e)}")

    # ------------------------------------------------------------------
    # Phase methods
    # ------------------------------------------------------------------

    def create_run(self, request: dict[str, Any]) -> GovernedRun:
        # If genome layers are present and a GenomeService is available,
        # register a real genome and derive genome_hash from the Merkle root.
        genome_hash = request.get("genome_hash")
        if self._genome_service is not None and not genome_hash:
            layer_keys = {
                "model_layer",
                "prompt_layer",
                "policy_layer",
                "watchtower_layer",
                "task_profile",
            }
            if layer_keys.issubset(request.keys()):
                result = self._genome_service.register_genome(
                    model_layer=request["model_layer"],
                    prompt_layer=request["prompt_layer"],
                    policy_layer=request["policy_layer"],
                    watchtower_layer=request["watchtower_layer"],
                    task_profile=request["task_profile"],
                    parent_genome_hash=request.get("parent_genome_hash"),
                )
                genome_hash = result["genome_hash"]

        run = GovernedRun(
            run_id=str(uuid.uuid4()),
            workspace_id=request.get("workspace_id", "default"),
            tenant_id=request.get("tenant_id", "default"),
            delegation_depth=int(request.get("delegation_depth", 0)),
            state=RunState.CREATED.value,
            request_payload=request,
            hashes={
                "genome_hash": genome_hash or sha256_json(request),
                "constitution_hash": request.get("constitution_hash")
                or sha256_json({"version": "1"}),
                "plan_hash": request.get("plan_hash") or sha256_json(request.get("prompt", "")),
            },
            scope=request.get("scope") or {"tools": ["llm.exec"]},
            approved_budget_cents=int(request.get("budget_approved_cents", 0)),
            execution_mode=request.get("execution_mode", "live"),
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
        payload = run.request_payload or {}
        from cappo_backend.services.authorization import normalize_directive

        normalized = normalize_directive(payload, strict=True)
        directive = normalized.directive
        risk_tier = normalized.risk_tier

        run.governance_decision = directive
        run.risk_tier = risk_tier
        run.v4_decision = {"directive": directive, "risk_tier": risk_tier}
        run.seked_state = {"directive": directive}
        self._transition(run, RunState.GOVERNED)
        if directive in _DENY_DIRECTIVES:
            raise GovernanceDeniedError(f"governance directive {directive!r} vetoed execution")

    def commit_run(self, run: GovernedRun) -> None:
        """Mint the PGL pre-certificate (commit point)."""
        governance_decision = _require_governance_decision(run)
        params = PreCertificateParams(
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            actor_id=run.request_payload.get("pgl_id") if run.request_payload else None,
            agent_id=run.request_payload.get("agent", {}).get("id")
            if run.request_payload
            else None,
            genome_hash=run.hashes.get("genome_hash", ""),
            constitution_hash=run.hashes.get("constitution_hash", ""),
            plan_hash=run.hashes.get("plan_hash", ""),
            governance_decision=governance_decision,
            risk_tier=run.risk_tier or "standard",
            approved_budget_cents=run.approved_budget_cents,
            reserve_cents=run.reserve_cents,
            input_hash=run.hashes.get("input_hash"),
            decision_frame_hash=run.hashes.get("decision_frame_hash"),
        )
        cert = self._pgl.mint_pre_certificate(params)
        run.pgl_identity = {
            "pre_execution_certificate_id": cert.certificate_id,
            "persisted": cert.persisted,
        }
        self._transition(run, RunState.COMMITTED)

    def mint_execution_identity(self, run: GovernedRun) -> None:
        """Mint ``ExecutionIdentityV1`` - strictly after commit, before route."""
        governance_decision = _require_governance_decision(run)
        ei_inputs: dict[str, Any] = {
            "pgl_pre_certificate_id": run.pgl_identity.get("pre_execution_certificate_id", ""),
            "genome_hash": run.hashes.get("genome_hash", ""),
            "constitution_hash": run.hashes.get("constitution_hash", ""),
            "plan_hash": run.hashes.get("plan_hash", ""),
            "tool_manifest_hash": run.hashes.get("tool_manifest_hash"),
            "delegation_chain_hash": run.hashes.get("delegation_chain_hash"),
            "input_hash": run.hashes.get("input_hash"),
            "seked_attestation_hash": sha256_json(run.seked_state),
            "directive": governance_decision,
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
            ei_id=identity["ei_id"],
            run_id=run.run_id,
            tenant_id=run.workspace_id,
            pgl_certificate_id=identity["pgl_certificate_id"],
            subject_json=identity["subject"],
            capabilities_json=identity["capabilities"],
            delegation_json=identity["delegation"],
            budget_json=identity["budget"],
            authority_bundle_hash=identity["authority_bundle_hash"],
            policy_hash=identity["policy_hash"],
            expires_at=datetime.fromisoformat(identity["expires_at"]),
            signature=identity["signature"],
            identity_json=identity,
        )
        self._db.add(ei_record)
        self._db.flush()

        self._transition(run, RunState.EI_MINTED)

    def route_run(self, run: GovernedRun) -> None:
        self._transition(run, RunState.ROUTED)

    def mint_eat(self, run: GovernedRun) -> None:
        """Mint Execution Authorization Token - strictly after EI, before route."""
        if self._gateway is None and self._eat_builder is None:
            return

        request = run.request_payload or {}
        ei = run.execution_identity
        if not ei:
            raise ValueError("Cannot mint EAT without an Execution Identity.")

        agent_id = request.get("agent", {}).get("id", "unknown")
        certificate_id = (run.pgl_identity or {}).get("pre_execution_certificate_id", "unknown")

        if self._eat_builder is not None:
            eat = self._eat_builder.build(
                execution_identity=ei,
                agent_id=agent_id,
                certificate_id=certificate_id,
                trust_score=float(request.get("trust_score", 75.0)),
                risk_tier=run.risk_tier or "standard",
            )
        else:
            eat = self._gateway.mint_eat(
                execution_identity=ei,
                agent_id=agent_id,
                certificate_id=certificate_id,
                trust_score=float(request.get("trust_score", 75.0)),
                risk_tier=run.risk_tier or "standard",
            )
        run.eat = eat
        self._db.flush()

        self._audit.record(
            "eat_minted",
            {
                "eat_id": eat["eat_id"],
                "execution_id": ei["execution_id"],
                "agent_id": agent_id,
                "ttl_seconds": eat["temporal"]["ttl_seconds"],
            },
            workspace_id=run.workspace_id,
            run_id=run.run_id,
        )

        self._transition(run, RunState.EAT_MINTED)

    def execute_run(self, run: GovernedRun) -> dict[str, Any]:
        """Enforce LAW 0, then run the executor.

        The gateway check fires *before* the side effect (the executor call):
        an invalid/missing EI raises ``EIValidationError`` while the run is still
        ROUTED, so ``run_governed`` transitions it to FAILED and no side effect
        occurs. This is the enforcement contract from the EI Plan -Enforcement
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
        on the ``execution_identities`` row and on ``run.pgl_identity`` instead -
        the signed identity object is never mutated post-issuance.
        """
        result = run.result_payload or {}
        output_hash = sha256_json(result)
        outcome_hash = sha256_json({"state": RunState.EXECUTED.value, "result": result})
        pre_cert_id = (run.pgl_identity or {}).get("pre_execution_certificate_id", "")
        governance_decision = _require_governance_decision(run)

        params = PostCertificateParams(
            pre_certificate_id=pre_cert_id,
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            actor_id=run.request_payload.get("pgl_id") if run.request_payload else None,
            agent_id=run.request_payload.get("agent", {}).get("id")
            if run.request_payload
            else None,
            genome_hash=run.hashes.get("genome_hash", ""),
            constitution_hash=run.hashes.get("constitution_hash", ""),
            plan_hash=run.hashes.get("plan_hash", ""),
            governance_decision=governance_decision,
            risk_tier=run.risk_tier or "standard",
            output_hash=output_hash,
            outcome_hash=outcome_hash,
            input_hash=run.hashes.get("input_hash"),
        )
        post_cert = self._pgl.mint_post_certificate(params)

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


def _normalize_directive(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise MissingGovernanceDecisionError(
            "CAPPO governance directive is required; missing decisions never default to ALLOW."
        )
    directive = raw.strip().upper()
    if directive not in _VALID_DIRECTIVES:
        raise MissingGovernanceDecisionError(
            f"Unsupported CAPPO governance directive {raw!r}; expected one of {sorted(_VALID_DIRECTIVES)}."
        )
    return directive


def _require_governance_decision(run: GovernedRun) -> str:
    directive = _normalize_directive(run.governance_decision)
    if directive not in _ALLOW_DIRECTIVES:
        raise GovernanceDeniedError(f"governance directive {directive!r} does not permit execution")
    return directive
