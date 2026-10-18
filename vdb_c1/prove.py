#!/usr/bin/env python3
"""Produce a recorded C1.0 proof run.

    python -m vdb_c1.prove --out vdb_c1/evidence/<run_id>

1. Finalizes one UPSERT normally.
2. Finalizes a second UPSERT through a real crash at `after_storage_commit`
   (process killed with os._exit), recovered by the retry.
3. Exports both bundles and verifies each in a separate process, started in
   an empty directory holding only verify.py, the bundle and the public
   trust store.

Writes bundles, the trust store, the verdicts and a manifest of file
digests. Private keys stay in a temporary directory and are deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent


def cli(*args):
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, "-m", "vdb_c1.cli", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)


def clean_room(bundle: pathlib.Path, trust: pathlib.Path) -> dict:
    with tempfile.TemporaryDirectory() as room:
        room = pathlib.Path(room)
        for src, name in ((HERE / "verify.py", "verify.py"), (bundle, "bundle.json"),
                          (trust, "trust_store.json")):
            shutil.copy(src, room / name)
        out = subprocess.run([sys.executable, "-I", "verify.py", "bundle.json", "trust_store.json"],
                             cwd=room, capture_output=True, text=True, timeout=60,
                             env={"PATH": os.environ.get("PATH", "")})
        return {"exit_code": out.returncode, "result": json.loads(out.stdout),
                "command": "python -I verify.py bundle.json trust_store.json",
                "room_contents": sorted(p.name for p in room.iterdir())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, required=True)
    out = ap.parse_args().out
    out.mkdir(parents=True)
    log = []
    with tempfile.TemporaryDirectory() as work:
        work = pathlib.Path(work)
        key, home = work / "authority.key", work / "home"
        log.append(cli("keygen", "--out", str(key)).stdout.strip())
        cli("init", "--home", str(home), "--namespace", "ns:veklom:c1:proof", "--authority-key", str(key))
        runs = {}
        for name, kv, crash in (("normal", ("alpha", "1"), None),
                                ("crash_c_recovered", ("beta", "2"), "after_storage_commit")):
            intent = work / f"{name}.intent.json"
            iid = json.loads(cli("sign-intent", "--key", str(key), "--home", str(home),
                                 "--set", *kv, "--out", str(intent)).stdout)["intent_id"]
            args = ["submit", "--home", str(home), "--intent", str(intent)]
            first = cli(*args, *(["--crash-at", crash] if crash else []))
            entry = {"intent_id": iid, "set": list(kv), "first_submit_exit": first.returncode,
                     "first_submit_stdout": first.stdout.strip()}
            if crash:
                db = sqlite3.connect(home / "state.db")
                entry["after_crash_generation"] = db.execute("SELECT generation FROM meta").fetchone()[0]
                entry["after_crash_applied"] = db.execute(
                    "SELECT count(*) FROM applied WHERE intent_id = ?", (iid,)).fetchone()[0]
                db.close()
                retry = cli(*args)
                entry["retry_result"] = json.loads(retry.stdout)["state"]
            bundle = out / f"{name}.bundle.json"
            cli("export", "--home", str(home), "--intent-id", iid, "--out", str(bundle))
            runs[name] = entry
        shutil.copy(home / "trust_store.json", out / "trust_store.json")
        state = json.loads(cli("state", "--home", str(home)).stdout)
        journal_kinds = [json.loads(line)["kind"] for line in
                         (home / "journal.jsonl").read_text().splitlines()]
    for name in runs:
        runs[name]["clean_room"] = clean_room(out / f"{name}.bundle.json", out / "trust_store.json")
    manifest = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(HERE.glob("*.py"))},
        "runs": runs, "final_state": state, "journal_kinds": journal_kinds,
        "files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(out.iterdir())},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    ok = all(r["clean_room"]["result"]["verdict"] == "VALID_FINALIZED" for r in runs.values())
    print(json.dumps({"out": str(out), "all_valid_finalized": ok}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
