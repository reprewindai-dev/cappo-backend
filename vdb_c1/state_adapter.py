"""The one physical consequence adapter: a SQLite WAL state store.

One namespace per store, one conditional UPSERT. The `applied` row is
written in the same transaction as the mutation, so the store itself records,
durably and at most once, whether an intent's consequence happened. Recovery
reads that row; it never re-executes.

Durability profile: journal_mode=WAL, synchronous=FULL. SQLite documents that
WAL with synchronous=NORMAL may roll back committed transactions after power
loss; FULL syncs the WAL on every commit. Both settings are read back on
every connection and recorded.
"""

from __future__ import annotations

import pathlib
import sqlite3
import uuid
from collections.abc import Callable

from vdb_c1 import canon

ADAPTER = "sqlite-wal/v1"


class PreconditionFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SqliteStateAdapter:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.execute_calls = 0
        self.commit_calls = 0

    @classmethod
    def create(cls, path: pathlib.Path, namespace: str) -> "SqliteStateAdapter":
        adapter = cls(path)
        conn = adapter._connect()
        try:
            conn.executescript("""
                BEGIN;
                CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK (id = 1), namespace TEXT NOT NULL,
                                   store_id TEXT NOT NULL, generation INTEGER NOT NULL);
                CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE applied (intent_id TEXT PRIMARY KEY, intent_hash TEXT NOT NULL,
                                      generation INTEGER NOT NULL UNIQUE, post_root TEXT NOT NULL);
                COMMIT;
            """)
            conn.execute("INSERT INTO meta VALUES (1, ?, ?, 0)", (namespace, str(uuid.uuid4())))
        finally:
            conn.close()
        return adapter

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        conn.execute("PRAGMA synchronous=FULL")
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        if mode != "wal" or sync != 2:
            conn.close()
            raise RuntimeError(f"durability profile not in force: journal_mode={mode} synchronous={sync}")
        return conn

    def durability(self) -> dict:
        return {"adapter": ADAPTER, "journal_mode": "wal", "synchronous": "FULL"}

    @staticmethod
    def _read(conn: sqlite3.Connection) -> dict:
        namespace, store_id, generation = conn.execute(
            "SELECT namespace, store_id, generation FROM meta WHERE id = 1").fetchone()
        entries = [[k, v] for k, v in conn.execute("SELECT key, value FROM kv ORDER BY key")]
        entries.sort()
        return {"namespace": namespace, "store_id": store_id, "generation": generation,
                "entries": entries, "root": canon.state_root(namespace, generation, entries)}

    def snapshot(self) -> dict:
        """A fresh connection's view of committed state, never in-process memory."""
        conn = self._connect()
        try:
            return self._read(conn)
        finally:
            conn.close()

    def applied(self, intent_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT intent_id, intent_hash, generation, post_root FROM applied "
                               "WHERE intent_id = ?", (intent_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return dict(zip(("intent_id", "intent_hash", "generation", "post_root"), row))

    def applied_count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT count(*) FROM applied").fetchone()[0]
        finally:
            conn.close()

    def apply_upsert(self, *, intent_id: str, intent_hash: str, key: str, value: str,
                     expected_generation: int, expected_root: str,
                     before_commit: Callable[[], None]) -> dict:
        """Conditional UPSERT in one transaction. `before_commit` runs after the
        mutation and before COMMIT (the protocol journals COMMITTING there)."""
        self.execute_calls += 1
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            pre = self._read(conn)
            if pre["generation"] != expected_generation:
                conn.execute("ROLLBACK")
                raise PreconditionFailed("E_STALE_GENERATION")
            if pre["root"] != expected_root:
                conn.execute("ROLLBACK")
                raise PreconditionFailed("E_ROOT_MISMATCH")
            conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))
            bumped = conn.execute("UPDATE meta SET generation = generation + 1 "
                                  "WHERE id = 1 AND generation = ?", (expected_generation,)).rowcount
            if bumped != 1:
                conn.execute("ROLLBACK")
                raise PreconditionFailed("E_STALE_GENERATION")
            post = self._read(conn)
            conn.execute("INSERT INTO applied VALUES (?, ?, ?, ?)",
                         (intent_id, intent_hash, post["generation"], post["root"]))
            before_commit()
            self.commit_calls += 1
            conn.execute("COMMIT")
            return {"post_generation": post["generation"], "post_root": post["root"]}
        finally:
            conn.close()
