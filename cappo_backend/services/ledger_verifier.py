"""Ledger chain verification — tamper-evidence for the audit + PGL ledgers.

EI Plan §Rollout Phase 4 ("structured LAW 0 audit logging"). The audit ledger
(:class:`AuditEvent`) and the per-certificate PGL ledger
(:class:`PGLLedgerEvent`) are hash-chained on write. This service re-derives the
hashes and walks the chains to prove they were not tampered with after the fact.

Two independent checks per chain:

1. **Node integrity** — recompute each row's hash from its own fields and confirm
   it matches the stored hash. A mismatch means the row's contents were altered.
2. **Link integrity** — follow ``previous_*_hash`` pointers from the genesis node
   and confirm every node is reachable exactly once with no dangling or forked
   links. This is independent of ``created_at`` ordering, so clock ties cannot
   produce a false positive or hide a removed row.

The recomputation mirrors exactly how the rows are minted:

* :class:`AuditEvent` — ``AuditService.record`` hashes
  ``{operation_type, workspace_id, run_id, payload, previous_log_hash}``.
* :class:`PGLLedgerEvent` — ``PGLClient._append_ledger_event`` hashes
  ``{**payload, previous_event_hash}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.services.canonical import sha256_json


@dataclass
class ChainReport:
    """Result of verifying a single hash chain."""

    name: str
    total: int = 0
    valid: bool = True
    broken_links: list[dict[str, str]] = field(default_factory=list)

    def fail(self, node_id: str, reason: str) -> None:
        self.valid = False
        self.broken_links.append({"id": node_id, "reason": reason})

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "total": self.total,
            "valid": self.valid,
            "broken_links": self.broken_links,
        }


class LedgerVerifier:
    """Re-derives and walks the audit + PGL ledger hash chains."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Audit ledger (global chain)
    # ------------------------------------------------------------------

    def verify_audit_chain(self) -> ChainReport:
        report = ChainReport(name="audit_events")
        events = list(
            self._db.execute(
                select(AuditEvent).order_by(AuditEvent.created_at.asc())
            ).scalars()
        )
        report.total = len(events)
        if not events:
            return report

        by_hash: dict[str, AuditEvent] = {}
        for ev in events:
            expected = sha256_json(
                {
                    "operation_type": ev.operation_type,
                    "workspace_id": ev.workspace_id,
                    "run_id": ev.run_id,
                    "payload": ev.payload,
                    "previous_log_hash": ev.previous_log_hash,
                }
            )
            if expected != ev.log_hash:
                report.fail(ev.log_id, "log_hash does not match recomputed hash")
            else:
                by_hash[ev.log_hash] = ev

        self._verify_links(
            report,
            events=events,
            by_hash=by_hash,
            node_id=lambda ev: ev.log_id,
            self_hash=lambda ev: ev.log_hash,
            prev_hash=lambda ev: ev.previous_log_hash,
        )
        return report

    # ------------------------------------------------------------------
    # PGL ledger (per-certificate chains)
    # ------------------------------------------------------------------

    def verify_pgl_chain(self, certificate_id: str) -> ChainReport:
        report = ChainReport(name=f"pgl_ledger:{certificate_id}")
        events = list(
            self._db.execute(
                select(PGLLedgerEvent)
                .where(PGLLedgerEvent.certificate_id == certificate_id)
                .order_by(PGLLedgerEvent.created_at.asc())
            ).scalars()
        )
        report.total = len(events)
        if not events:
            return report

        by_hash: dict[str, PGLLedgerEvent] = {}
        for ev in events:
            expected = sha256_json(
                {**ev.payload, "previous_event_hash": ev.previous_event_hash}
            )
            if expected != ev.event_hash:
                report.fail(ev.event_id, "event_hash does not match recomputed hash")
            else:
                by_hash[ev.event_hash] = ev

        self._verify_links(
            report,
            events=events,
            by_hash=by_hash,
            node_id=lambda ev: ev.event_id,
            self_hash=lambda ev: ev.event_hash,
            prev_hash=lambda ev: ev.previous_event_hash,
        )
        return report

    def verify_all_pgl_chains(self) -> list[ChainReport]:
        cert_ids = list(
            self._db.execute(
                select(PGLLedgerEvent.certificate_id).distinct()
            ).scalars()
        )
        return [self.verify_pgl_chain(cid) for cid in sorted(cert_ids)]

    # ------------------------------------------------------------------
    # Full verification
    # ------------------------------------------------------------------

    def verify_all(self) -> dict[str, object]:
        audit = self.verify_audit_chain()
        pgl = self.verify_all_pgl_chains()
        chains = [audit] + pgl
        return {
            "valid": all(c.valid for c in chains),
            "chains": [c.as_dict() for c in chains],
        }

    # ------------------------------------------------------------------
    # Shared link walker
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_links(
        report: ChainReport,
        *,
        events,
        by_hash,
        node_id,
        self_hash,
        prev_hash,
    ) -> None:
        """Walk pointers from the genesis node and confirm a single intact chain.

        Operates only on nodes whose own hash already verified (``by_hash``); a
        node with a bad hash is reported separately and excluded here so we do
        not double-count it.
        """
        genesis = [ev for ev in events if prev_hash(ev) is None]
        if len(genesis) == 0:
            report.fail("<chain>", "no genesis node (every node references a parent)")
            return
        if len(genesis) > 1:
            for ev in genesis:
                report.fail(node_id(ev), "multiple genesis nodes (forked chain)")
            return

        # Index children by their previous-hash pointer; detect forks.
        children: dict[str, list] = {}
        for ev in events:
            ph = prev_hash(ev)
            if ph is not None:
                children.setdefault(ph, []).append(ev)

        visited = 0
        current = genesis[0]
        while current is not None:
            if self_hash(current) not in by_hash:
                # node integrity already failed; cannot trust its pointer
                return
            visited += 1
            next_nodes = children.get(self_hash(current), [])
            if len(next_nodes) > 1:
                for ev in next_nodes:
                    report.fail(node_id(ev), "chain forks: multiple nodes share a parent")
                return
            current = next_nodes[0] if next_nodes else None

        if visited != len(events):
            report.fail(
                "<chain>",
                f"chain reachable={visited} but total={len(events)} "
                "(broken link or orphaned node)",
            )
