#!/usr/bin/env python3
"""VRE-1C: concurrent cold materialization.

    sudo python -m vre1.concurrent --tenants 64 --rounds 10

Builds `--tenants` distinct dormant tenants (4 MiB logical, 25% density,
sparse-zlib1, as in VRE-1B). For each K in 1, 2, 4, 8, 16 and each round:

1. starts K restore probes on K distinct random tenants;
2. waits until all K print READY;
3. drops the page cache once;
4. sends "go" to all K back to back.

Each probe records its own restore latency, first-request latency, resident
memory and integrity (it serves the next request and must match the
original). Reported per K: per-restore latency under contention, the wall
time for the whole batch, and the summed settled resident memory of the
batch. That is what "logical ÷ resident" becomes when tenants are live at
the same time rather than one at a time.

Results go to vre1/runs/<run_id>-concurrent/.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from vre1.bench import PROBE, drop_caches, probe, stats

HERE = pathlib.Path(__file__).resolve().parent
CONCURRENCY = (1, 2, 4, 8, 16)
LOGICAL_PAGES = 64


def batch(specs: list[dict]) -> tuple[list[dict], float]:
    procs = [subprocess.Popen([sys.executable, "-I", str(PROBE), "restore", json.dumps(s)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for s in specs]
    for p in procs:
        if p.stdout.readline().strip() != "READY":
            for q in procs:
                q.kill()
            raise RuntimeError(p.stderr.read())
    drop_caches()
    t0 = time.perf_counter()
    for p in procs:
        p.stdin.write("go\n")
        p.stdin.flush()
    out = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=600)
        if p.returncode != 0:
            raise RuntimeError(stderr)
        out.append(json.loads(stdout.strip().splitlines()[-1]))
    return out, (time.perf_counter() - t0) * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenants", type=int, default=64)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--out", type=pathlib.Path, default=HERE / "runs")
    args = ap.parse_args()
    if os.geteuid() != 0:
        print("needs root to drop the page cache", file=sys.stderr)
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-concurrent"
    out = args.out / run_id
    out.mkdir(parents=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="vre1c-"))
    rows, samples = [], open(out / "samples.jsonl", "w")
    try:
        made = probe("make-many", {"dir": str(work), "cwasm": str(work / "m.cwasm"), "tenants": args.tenants,
                                   "pages": LOGICAL_PAGES, "density": 250, "steps": 100, "codec": "sparse-zlib1"})
        tenants = made["tenants"]
        rng = random.Random(20261011)
        for k in CONCURRENCY:
            per, walls, resident = [], [], []
            failures = 0
            for rnd in range(args.rounds):
                chosen = rng.sample(tenants, k)
                results, wall = batch([{"path": t["path"], "cwasm": str(work / "m.cwasm"),
                                        "digest_after_next": t["digest_after_next"],
                                        "memory_sha256_after_next": t["memory_sha256_after_next"]}
                                       for t in chosen])
                walls.append(wall)
                resident.append(sum(r["rss_settled_delta_kib"] for r in results) * 1024)
                for r in results:
                    r.update(concurrency=k, round=rnd)
                    samples.write(json.dumps(r) + "\n")
                    failures += not r["integrity_ok"]
                    per.append(r["restore_ms"])
            logical = k * made["logical_bytes_each"]
            rows.append({
                "concurrency": k,
                "restore_ms_per_tenant": stats(per),
                "batch_wall_ms": stats(walls),
                "batch_settled_resident_bytes_p50": sorted(resident)[len(resident) // 2],
                "logical_bytes_live": logical,
                "logical_over_resident_live": round(logical / sorted(resident)[len(resident) // 2], 2),
                "integrity_failures": failures,
            })
            print(f"K={k:<3} per-tenant p50/p95 = {rows[-1]['restore_ms_per_tenant']['p50']}/"
                  f"{rows[-1]['restore_ms_per_tenant']['p95']} ms  batch p50 = "
                  f"{rows[-1]['batch_wall_ms']['p50']} ms  failures={failures}", flush=True)
    finally:
        samples.close()
        shutil.rmtree(work, ignore_errors=True)
    from importlib.metadata import version
    (out / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "finished_at": datetime.now(timezone.utc).isoformat(),
        "host": {"kernel": platform.release(), "nproc": os.cpu_count(), "python": sys.version.split()[0],
                 "wasmtime_py": version("wasmtime")},
        "tenants": args.tenants, "rounds": args.rounds, "logical_mib_each": LOGICAL_PAGES * 64 / 1024,
        "summary": {"vre1c": rows},
    }, indent=1))
    print(f"run written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
