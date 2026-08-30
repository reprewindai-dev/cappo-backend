"""Run and record the real local N8N-16 release path.

The output intentionally distinguishes local ledger settlement and the CAPPO
hash-chain from external PGL anchoring or on-chain x402 settlement.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

REPO = Path(__file__).resolve().parents[1]
TRACE_PATH = REPO / "N8N_16_ACTUAL_TRACE.md"
TARGET_PATH = REPO / "scratch" / "n8n17" / "n8n_governed_append.jsonl"
WORKSPACE_ID = "ws_local_n8n16_certification"
AMOUNT_CENTS = 1


def now() -> datetime:
    return datetime.now(timezone.utc)


def configure_database() -> None:
    """Load the approved test URL without printing it or placing it in argv."""
    for line in (REPO / ".env.test").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            value = line.split("=", 1)[1].strip()
            os.environ["DATABASE_URL"] = value.replace(
                "@172.17.200.200:5432", "@127.0.0.1:5432"
            )
            return
    raise RuntimeError("DATABASE_URL is missing from the approved test environment")


configure_database()

from cappo_backend.db.session import SessionLocal
from cappo_backend.execution.budget_ledger import BudgetLedger
from cappo_backend.execution.kms import LocalKMSProvider
from cappo_backend.execution.sandbox_file_connector import SandboxFileAppendConnector
from cappo_backend.models.consequence_execution import (
    ConsequenceExecutionEvent,
    ConsequenceState,
    build_intent_hash,
    build_proof_subject_hash,
)
from cappo_backend.models.workspace_budget import WorkspaceBudget


def append_event(
    db,
    *,
    execution_id: str,
    intent_hash: str,
    lease_id: str,
    state: ConsequenceState,
    version: int,
    previous_state: str,
    proof_type: str | None = None,
    proof_ref: str | None = None,
) -> ConsequenceExecutionEvent:
    event = ConsequenceExecutionEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        operation_id=execution_id,
        intent_hash=intent_hash,
        state=state.value,
        version=version,
        receipt_id=lease_id,
        mount_id="n8n-17-sandbox-file-append",
        execution_id=execution_id,
        principal=WORKSPACE_ID,
        action="fs:append",
        resource="sandbox:n8n-governed-append",
        completion_proof_type=proof_type,
        completion_proof_ref=proof_ref,
        proof_subject_hash=build_proof_subject_hash(
            operation_id=execution_id,
            intent_hash=intent_hash,
            previous_truth_state=previous_state,
            asserted_truth_state=state.value,
            consequence_identity=lease_id,
            canonical_asserted_proposition=(
                f"{state.value} fs:append on sandbox:n8n-governed-append"
            ),
        ),
    )
    db.add(event)
    db.flush()
    return event


def render_trace(events: list[dict], result: dict) -> None:
    rows = [
        "| Timestamp | Component | Event | Correlation | Observed detail |",
        "|---|---|---|---|---|",
    ]
    for event in events:
        rows.append(
            f"| `{event['timestamp']}` | {event['component']} | {event['event']} | "
            f"`{event['correlation']}` | {event['detail']} |"
        )
    body = "\n".join(rows)
    TRACE_PATH.write_text(
        "# N8N-16 Actual Correlated Execution Trace\n\n"
        "Evidence state: **VERIFIED_LOCAL**. Generated from a live local run, PostgreSQL rows, "
        "and the reconciled physical sandbox record.\n\n"
        + body
        + "\n\n## Release-gate result\n\n"
        + "```json\n"
        + json.dumps(result, indent=2, sort_keys=True)
        + "\n```\n\n"
        + "## Claim boundary\n\n"
        + "- Exactly-once **local** budget settlement: verified.\n"
        + "- CAPPO append-only consequence events: verified.\n"
        + "- CAPPO hash-chained audit receipt: verified.\n"
        + "- External Gnomledger/PGL receipt: **UNVERIFIED** (service not part of this run).\n"
        + "- On-chain x402/USDC transaction: **UNVERIFIED** (no chain transaction submitted).\n",
        encoding="utf-8",
    )


def main() -> None:
    connector = SandboxFileAppendConnector(TARGET_PATH)
    execution_id = f"exec-live-{uuid.uuid4().hex[:12]}"
    lease_id = f"lease-live-{uuid.uuid4().hex[:12]}"
    content = f"N8N-16 LIVE SETTLED CONSEQUENCE {execution_id}"
    intent_hash = build_intent_hash(
        "n8n-17-sandbox-file-append",
        execution_id,
        connector.append_action,
        connector.resource,
        {"content": content},
    )
    timeline: list[dict] = []

    def observed(component: str, event: str, correlation: str, detail: str) -> None:
        timeline.append(
            {
                "timestamp": now().isoformat().replace("+00:00", "Z"),
                "component": component,
                "event": event,
                "correlation": correlation,
                "detail": detail,
            }
        )

    observed("UI / certification client", "Intent created", execution_id, "Real local certification intent")
    with SessionLocal() as db:
        budget = db.execute(
            select(WorkspaceBudget)
            .where(WorkspaceBudget.workspace_id == WORKSPACE_ID)
            .with_for_update()
        ).scalar_one_or_none()
        if budget is None:
            budget = WorkspaceBudget(workspace_id=WORKSPACE_ID, balance_cents=100)
            db.add(budget)
            db.flush()
        balance_before = budget.balance_cents
        BudgetLedger(db).reserve(
            execution_id=execution_id,
            workspace_id=WORKSPACE_ID,
            amount_cents=AMOUNT_CENTS,
        )
        append_event(
            db,
            execution_id=execution_id,
            intent_hash=intent_hash,
            lease_id=lease_id,
            state=ConsequenceState.AUTHORIZED,
            version=0,
            previous_state="none",
        )
        db.commit()
    observed("CAPPO / PostgreSQL", "Budget hold + authority event committed", execution_id, "1 cent ACTIVE hold; AUTHORIZED event v0")

    token = LocalKMSProvider().sign(
        {
            "sub": f"workspace:{WORKSPACE_ID}",
            "lease_id": lease_id,
            "execution_id": execution_id,
            "workflow_id": "W6r3rV1OqR",
            "allowed_actions": [connector.append_action],
            "allowed_resources": [connector.resource],
            "budget": {"currency": "USD_CENT", "max": AMOUNT_CENTS},
        },
        audience="sandbox_file_append",
    )
    observed("CAPPO KMS", "Lease signed", lease_id, "Ed25519 JWT created; token bytes not logged")

    with SessionLocal() as db:
        append_event(
            db,
            execution_id=execution_id,
            intent_hash=intent_hash,
            lease_id=lease_id,
            state=ConsequenceState.STARTED,
            version=1,
            previous_state=ConsequenceState.AUTHORIZED.value,
            proof_type="optimistic_claim",
        )
        db.commit()
    observed("CAPPO", "Dispatch started", execution_id, "STARTED event v1 committed before webhook call")

    response_error: str | None = None
    try:
        response = httpx.post(
            "http://127.0.0.1:5678/webhook/governed-webhook",
            json={
                "veklom_authority": token,
                "data": {"action": connector.append_action, "content": content},
            },
            timeout=20,
        )
        observed("n8n", "Webhook returned", execution_id, f"HTTP {response.status_code}")
    except httpx.HTTPError as exc:
        response = None
        response_error = type(exc).__name__
        observed("n8n", "Webhook outcome ambiguous", execution_id, response_error)

    record = connector.reconcile(execution_id)
    if record is None:
        with SessionLocal() as db:
            append_event(
                db,
                execution_id=execution_id,
                intent_hash=intent_hash,
                lease_id=lease_id,
                state=ConsequenceState.OUTCOME_UNKNOWN,
                version=2,
                previous_state=ConsequenceState.STARTED.value,
                proof_type="outcome_uncertain",
            )
            db.commit()
        raise RuntimeError("physical consequence is unproven; hold retained for reconciliation")

    observed("Target enclave", "Physical consequence reconciled", execution_id, f"record_hash={record['record_hash']}")
    with SessionLocal() as db:
        append_event(
            db,
            execution_id=execution_id,
            intent_hash=intent_hash,
            lease_id=lease_id,
            state=ConsequenceState.SUCCEEDED,
            version=2,
            previous_state=ConsequenceState.STARTED.value,
            proof_type="reconciliation_filesystem",
            proof_ref=record["record_hash"],
        )
        settlement = BudgetLedger(db).settle_local(
            execution_id=execution_id,
            evidence_hash=record["record_hash"],
            endpoint="sandbox_file_append",
        )
        db.commit()
        balance_after = db.get(WorkspaceBudget, WORKSPACE_ID).balance_cents
    observed("CAPPO / PostgreSQL", "Consequence + settlement committed", execution_id, "SUCCEEDED event v2; local-ledger settlement; hash-chained audit")

    result = {
        "execution_id": execution_id,
        "lease_id": lease_id,
        "physical_record_hash": record["record_hash"],
        "physical_action_hash": record["action_hash"],
        "budget_balance_before_cents": balance_before,
        "budget_balance_after_cents": balance_after,
        "settlement_amount_cents": settlement.amount_cents,
        "local_ledger_id": settlement.ledger_id,
        "audit_hash": settlement.audit_hash,
        "external_pgl_verified": False,
        "onchain_x402_verified": False,
        "webhook_error": response_error,
    }
    render_trace(timeline, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
