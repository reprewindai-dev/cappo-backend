"""Outbox Dispatcher — reliably delivers refinery_outbox rows to Cloudflare Queues.

Design
──────
This dispatcher runs as a periodic background task (e.g., via APScheduler or
a simple asyncio loop). It polls ``refinery_outbox`` for undelivered rows and
pushes them to the Cloudflare Queue REST API, marking each row as dispatched
only AFTER a successful delivery acknowledgement.

Idempotency
───────────
Idempotency identity = (event_id, evidence_hash, operation, schema_version).
``attempt_count`` is DELIVERY METADATA — it tracks how many times we've tried
but is NOT part of the idempotency key. This matters because:

    retry #1 payload = { event_id: X, attempt_count: 1 }
    retry #2 payload = { event_id: X, attempt_count: 2 }

If attempt_count were part of the key, retry #2 would look like a new job to
the refinery consumer, defeating deduplication.

Correct behaviour: both retries carry the same stable identity
(event_id + evidence_hash + operation + schema_version), and the consumer
uses that stable key to deduplicate. Same key → no second computation.

Cloudflare Queues is at-least-once delivery — duplicate messages are possible
after network failures. The consumer must handle them; the dispatcher should
not pretend it has built exactly-once delivery.

Crash window (acknowledged and handled):
    CF accepts message → dispatcher crashes → DB never gets status=sent
    → message delivered again → consumer deduplicates via event_id → no harm

This gives: reliable at-least-once transport + idempotent processing.

Outbox Schema
─────────────
    id              SERIAL PRIMARY KEY
    event_id        TEXT UNIQUE NOT NULL    -- stable dedup identity
    evidence_hash   TEXT NOT NULL           -- stable dedup identity
    operation       TEXT NOT NULL           -- stable dedup identity
    schema_version  TEXT NOT NULL           -- stable dedup identity
    payload         JSONB NOT NULL
    status          TEXT DEFAULT 'pending'  -- pending / sent / dlq
    attempt_count   INTEGER DEFAULT 0       -- delivery metadata, NOT dedup key
    available_at    TIMESTAMPTZ DEFAULT now()
    locked_at       TIMESTAMPTZ
    sent_at         TIMESTAMPTZ
    last_error      TEXT
    created_at      TIMESTAMPTZ DEFAULT now()

Transactional correctness note
───────────────────────────────
The evidence + outbox INSERT is atomic only because both writes target the
same PostgreSQL database. If an external ledger (PGL, chain anchor) must
also acknowledge the evidence, that remote operation is NOT part of the
Postgres transaction — Postgres cannot make a network call atomic with a
local commit. Model external acknowledgements as separate state transitions
(e.g., a second outbox row with operation="pgl.anchor") rather than claiming
the whole distributed process is atomic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Maximum delivery attempts before moving to DLQ
MAX_ATTEMPTS = 10

# Cloudflare Queue REST endpoint template
CF_QUEUE_URL_TEMPLATE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/queues/{queue_id}/messages"
)


async def dispatch_pending(
    db_session: Any,
    cf_account_id: str,
    cf_queue_id: str,
    cf_api_token: str,
    batch_size: int = 50,
) -> int:
    """Poll refinery_outbox and dispatch pending rows to Cloudflare Queue.

    Returns the number of rows successfully dispatched in this cycle.
    """
    rows = await _fetch_pending(db_session, batch_size)
    if not rows:
        return 0

    dispatched = 0
    queue_url = CF_QUEUE_URL_TEMPLATE.format(
        account_id=cf_account_id,
        queue_id=cf_queue_id,
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        for row in rows:
            row_id = row["id"]
            event_id = row["event_id"]
            attempt_count = row["attempt_count"]

            if attempt_count >= MAX_ATTEMPTS:
                logger.error(
                    "outbox_row=%s event_id=%s exceeded MAX_ATTEMPTS=%d — moving to DLQ",
                    row_id, event_id, MAX_ATTEMPTS,
                )
                await _move_to_dlq(db_session, row)
                continue

            # ── Stable idempotency identity ──────────────────────────────────
            # The consumer uses (event_id, evidence_hash, operation, schema_version)
            # to deduplicate. attempt_count is excluded — it's delivery metadata.
            message_body = {
                "event_id": event_id,
                "evidence_hash": row["evidence_hash"],
                "operation": row["operation"],
                "schema_version": row["schema_version"],
                # Payload is attached for the consumer's use but is NOT part
                # of the idempotency key.
                "payload": row["payload"],
            }

            try:
                resp = await client.post(
                    queue_url,
                    json={"messages": [{"body": message_body}]},
                    headers={
                        "Authorization": f"Bearer {cf_api_token}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()

                # Mark as sent ONLY after CF acknowledges the message.
                # Crash between this ack and DB update → message re-delivered
                # → consumer deduplicates → safe.
                await _mark_sent(db_session, row_id)
                dispatched += 1
                logger.debug(
                    "Dispatched outbox_row=%s event_id=%s to CF Queue (attempt=%d)",
                    row_id, event_id, attempt_count + 1,
                )

            except httpx.HTTPError as exc:
                logger.warning(
                    "CF Queue delivery failed for outbox_row=%s event_id=%s attempt=%d: %s",
                    row_id, event_id, attempt_count, exc,
                )
                await _increment_attempt(db_session, row_id, str(exc))

    return dispatched


async def _fetch_pending(db_session: Any, limit: int) -> list[dict[str, Any]]:
    """Fetch undelivered outbox rows, skipping locked (already claimed) rows."""
    result = await db_session.execute(
        """
        SELECT id, event_id, evidence_hash, operation, schema_version,
               payload, attempt_count
        FROM refinery_outbox
        WHERE status = 'pending'
          AND attempt_count < :max_attempts
          AND available_at <= now()
        ORDER BY created_at ASC
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
        """,
        {"max_attempts": MAX_ATTEMPTS, "limit": limit},
    )
    return [dict(row) for row in result.fetchall()]


async def _mark_sent(db_session: Any, row_id: int) -> None:
    await db_session.execute(
        "UPDATE refinery_outbox SET status = 'sent', sent_at = now() WHERE id = :id",
        {"id": row_id},
    )


async def _increment_attempt(
    db_session: Any, row_id: int, error: str, backoff_seconds: int = 30
) -> None:
    """Increment attempt_count and push available_at forward with a backoff."""
    await db_session.execute(
        """
        UPDATE refinery_outbox
        SET attempt_count = attempt_count + 1,
            last_error = :error,
            available_at = now() + (:backoff || ' seconds')::interval
        WHERE id = :id
        """,
        {"id": row_id, "error": error[:1024], "backoff": backoff_seconds},
    )


async def _move_to_dlq(db_session: Any, row: dict[str, Any]) -> None:
    """Move a row that exceeded MAX_ATTEMPTS to the dead-letter queue table."""
    try:
        await db_session.execute(
            """
            INSERT INTO refinery_outbox_dlq
                (event_id, evidence_hash, operation, schema_version,
                 payload, attempt_count, failed_at, last_error)
            VALUES
                (:event_id, :evidence_hash, :operation, :schema_version,
                 :payload::jsonb, :attempt_count, now(), :last_error)
            ON CONFLICT (event_id) DO NOTHING
            """,
            {
                **row,
                "payload": json.dumps(row["payload"]),
                "last_error": row.get("last_error", ""),
            },
        )
        await db_session.execute(
            "UPDATE refinery_outbox SET status = 'dlq' WHERE id = :id",
            {"id": row["id"]},
        )
    except Exception:
        logger.exception("Failed to move outbox_row=%s to DLQ", row["id"])
