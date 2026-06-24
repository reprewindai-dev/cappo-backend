"""PGLClient — mints and persists PGL certificates.

Forward-constructed (migration note §2): there is no legacy implementation to
port. The client mints :class:`PGLCertificate` rows and chains a
:class:`PGLLedgerEvent`, with an explicit ``persisted: bool`` contract.

Task 6 — production fail-closed guard: when ``CAPPO_REQUIRE_PERSISTENT_PGL`` is
true a missing DB session is fatal. There is deliberately **no** silent-simulation
path usable in production; a non-persisted certificate can only be produced in
development and is clearly marked ``persisted=False``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from cappo_backend.config import Settings, get_settings
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.services.canonical import sha256_json


@dataclass
class PreCertificateParams:
    run_id: str
    workspace_id: str
    genome_hash: str
    constitution_hash: str
    plan_hash: str
    governance_decision: str
    risk_tier: str
    actor_id: str | None = None
    agent_id: str | None = None
    approved_budget_cents: int = 0
    reserve_cents: int = 0
    input_hash: str | None = None
    decision_frame_hash: str | None = None
    provenance: dict[str, Any] | None = None


@dataclass
class PostCertificateParams:
    pre_certificate_id: str
    run_id: str
    workspace_id: str
    genome_hash: str
    constitution_hash: str
    plan_hash: str
    governance_decision: str
    risk_tier: str
    output_hash: str
    outcome_hash: str
    actor_id: str | None = None
    agent_id: str | None = None
    input_hash: str | None = None
    provenance: dict[str, Any] | None = None


class PGLPersistenceError(RuntimeError):
    """Raised when production requires a persisted PGL certificate but none is possible."""


class PGLClient:
    def __init__(self, db: Session | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # Task 6: fail-closed guard. Mirrors the EI Implementation Plan's
        # PGLClient.__init__ guard, wired to typed settings.
        if self._settings.cappo_require_persistent_pgl and db is None:
            raise PGLPersistenceError(
                "PGL simulation fallback is forbidden when CAPPO_REQUIRE_PERSISTENT_PGL "
                "is set. Provide a DB session."
            )
        self._db = db

    @property
    def persistent(self) -> bool:
        return self._db is not None

    def mint_pre_certificate(
        self,
        params: PreCertificateParams,
    ) -> PGLCertificate:
        """Mint a pre-execution PGL certificate.

        Persisted when a DB session is available; otherwise returns a transient
        certificate flagged ``persisted=False`` (development only — the
        constructor guard prevents this in production).
        """
        cert = PGLCertificate(
            certificate_id=str(uuid.uuid4()),
            run_id=params.run_id,
            workspace_id=params.workspace_id,
            actor_id=params.actor_id,
            agent_id=params.agent_id,
            genome_hash=params.genome_hash,
            constitution_hash=params.constitution_hash,
            plan_hash=params.plan_hash,
            input_hash=params.input_hash,
            decision_frame_hash=params.decision_frame_hash,
            governance_decision=params.governance_decision,
            risk_tier=params.risk_tier,
            approved_budget_cents=params.approved_budget_cents,
            reserve_cents=params.reserve_cents,
            provenance_json=params.provenance or {},
            persisted=self.persistent,
        )

        if self._db is not None:
            self._db.add(cert)
            self._db.flush()
            self._append_ledger_event(cert, "pre_certificate_minted")

        return cert

    def mint_post_certificate(
        self,
        params: PostCertificateParams,
    ) -> PGLCertificate:
        """Mint a post-execution PGL certificate linked back to the pre-cert.

        Records the execution ``output_hash`` and ``outcome_hash`` and chains a
        ``post_certificate_minted`` ledger event. The pre-certificate is updated
        with a forward ``post_execution_certificate_id`` link (migration note
        §1.4 pre/post linkage).
        """
        cert = PGLCertificate(
            certificate_id=str(uuid.uuid4()),
            run_id=params.run_id,
            workspace_id=params.workspace_id,
            actor_id=params.actor_id,
            agent_id=params.agent_id,
            pre_execution_certificate_id=params.pre_certificate_id,
            genome_hash=params.genome_hash,
            constitution_hash=params.constitution_hash,
            plan_hash=params.plan_hash,
            input_hash=params.input_hash,
            output_hash=params.output_hash,
            outcome_hash=params.outcome_hash,
            governance_decision=params.governance_decision,
            risk_tier=params.risk_tier,
            provenance_json=params.provenance or {},
            persisted=self.persistent,
        )

        if self._db is not None:
            self._db.add(cert)
            self._db.flush()
            pre = self._db.get(PGLCertificate, params.pre_certificate_id)
            if pre is not None:
                pre.post_execution_certificate_id = cert.certificate_id
                self._db.flush()
            self._append_ledger_event(cert, "post_certificate_minted")

        return cert

    def get_certificate(self, certificate_id: str) -> PGLCertificate | None:
        if self._db is None:
            return None
        return self._db.get(PGLCertificate, certificate_id)

    def _append_ledger_event(self, cert: PGLCertificate, event_type: str) -> PGLLedgerEvent:
        assert self._db is not None
        previous = (
            self._db.query(PGLLedgerEvent)
            .filter(PGLLedgerEvent.certificate_id == cert.certificate_id)
            .order_by(PGLLedgerEvent.created_at.desc())
            .first()
        )
        previous_hash = previous.event_hash if previous else None
        payload = {
            "certificate_id": cert.certificate_id,
            "event_type": event_type,
            "genome_hash": cert.genome_hash,
            "plan_hash": cert.plan_hash,
        }
        event = PGLLedgerEvent(
            certificate_id=cert.certificate_id,
            event_type=event_type,
            payload=payload,
            previous_event_hash=previous_hash,
            event_hash=sha256_json({**payload, "previous_event_hash": previous_hash}),
        )
        self._db.add(event)
        self._db.flush()
        return event
