"""Append-only, hash-chained protocol journal, separate from the state store.

One record per line (JCS bytes + newline). Each append is fsynced before the
caller continues, so a record that returns is durable. A torn final line (a
crash mid-write, no trailing newline) is dropped on open; any other
inconsistency raises JournalCorrupt and the coordinator refuses to run.
"""

from __future__ import annotations

import json
import os
import pathlib

from vdb_c1 import canon

JOURNAL_PROTOCOL = "veklom.vdb.c1.journal/v1"


class JournalCorrupt(Exception):
    pass


def record_hash(record: dict) -> str:
    return canon.digest({k: v for k, v in record.items() if k != "hash"})


class Journal:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.torn_tail_bytes = 0
        self.records: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = self.path.read_bytes()
        if data and not data.endswith(b"\n"):
            keep = data.rfind(b"\n") + 1
            self.torn_tail_bytes = len(data) - keep
            with open(self.path, "r+b") as f:
                f.truncate(keep)
                os.fsync(f.fileno())
            data = data[:keep]
        records, prev = [], "genesis"
        for seq, line in enumerate(data.splitlines()):
            try:
                rec = json.loads(line)
            except ValueError as e:
                raise JournalCorrupt(f"line {seq}: not JSON") from e
            if (rec.get("protocol") != JOURNAL_PROTOCOL or rec.get("seq") != seq
                    or rec.get("prev") != prev or rec.get("hash") != record_hash(rec)
                    or canon.jcs(rec) != line):
                raise JournalCorrupt(f"line {seq}: chain or hash mismatch")
            records.append(rec)
            prev = rec["hash"]
        return records

    def append(self, kind: str, intent_id: str, body: dict, at: str) -> dict:
        rec = {
            "protocol": JOURNAL_PROTOCOL,
            "seq": len(self.records),
            "prev": self.records[-1]["hash"] if self.records else "genesis",
            "at": at,
            "kind": kind,
            "intent_id": intent_id,
            "body": body,
        }
        rec["hash"] = record_hash(rec)
        new_file = not self.path.exists()
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, canon.jcs(rec) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        if new_file:
            dfd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        self.records.append(rec)
        return rec

    def for_intent(self, intent_id: str) -> list[dict]:
        return [r for r in self.records if r["intent_id"] == intent_id]
