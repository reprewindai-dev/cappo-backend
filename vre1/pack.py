#!/usr/bin/env python3
"""VRE-1E: many live tenants per runtime process.

    sudo python -m vre1.pack --tenants 500 --rounds 3

VRE-1C showed that one process per live tenant puts about 3 MB of runtime
under every tenant, so live logical / resident is about 1.0x. Here K
tenants are restored into K separate wasmtime stores that share one engine
and one compiled module, all live at the same time in one process (cold page
cache first). Measured: settled resident memory with all K live, per-tenant
restore latency, the latency of one request on an already-live tenant, and
integrity for every tenant.

Results go to vre1/runs/<run_id>-pack/.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import random
import shutil
import sys
import tempfile
from datetime import datetime, timezone

from vre1.bench import drop_caches, probe, stats

HERE = pathlib.Path(__file__).resolve().parent
PACK_SIZES = (1, 8, 32, 128, 500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenants", type=int, default=500)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", type=pathlib.Path, default=HERE / "runs")
    args = ap.parse_args()
    if os.geteuid() != 0:
        print("needs root to drop the page cache", file=sys.stderr)
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-pack"
    out = args.out / run_id
    out.mkdir(parents=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="vre1e-"))
    rows, samples = [], open(out / "samples.jsonl", "w")
    try:
        made = probe("make-many", {"dir": str(work), "cwasm": str(work / "m.cwasm"), "tenants": args.tenants,
                                   "pages": 64, "density": 250, "steps": 100, "codec": "sparse-zlib1"})
        rng = random.Random(20261011)
        for k in [s for s in PACK_SIZES if s <= args.tenants]:
            restore_ms, request_us, resident, failures = [], [], [], 0
            for rnd in range(args.rounds):
                drop_caches()
                r = probe("pack", {"cwasm": str(work / "m.cwasm"), "tenants": rng.sample(made["tenants"], k)})
                r.update(round=rnd)
                samples.write(json.dumps(r) + "\n")
                restore_ms += r["restore_ms"]
                request_us += r["request_us"]
                resident.append(r["resident_kib_all_live"] * 1024)
                failures += r["integrity_failures"]
            logical = k * made["logical_bytes_each"]
            res = sorted(resident)[len(resident) // 2]
            rows.append({"k_live": k, "restore_ms_per_tenant": stats(restore_ms),
                         "request_us_live": stats(request_us), "resident_bytes_all_live_p50": res,
                         "resident_bytes_per_tenant": round(res / k), "logical_bytes_live": logical,
                         "logical_over_resident_live": round(logical / res, 2),
                         "integrity_failures": failures})
            print(f"K={k:<4} resident/tenant={rows[-1]['resident_bytes_per_tenant'] / 1e6:.2f} MB "
                  f"ratio={rows[-1]['logical_over_resident_live']} restore p95="
                  f"{rows[-1]['restore_ms_per_tenant']['p95']} ms failures={failures}", flush=True)
    finally:
        samples.close()
        shutil.rmtree(work, ignore_errors=True)
    from importlib.metadata import version
    (out / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "finished_at": datetime.now(timezone.utc).isoformat(),
        "host": {"kernel": platform.release(), "nproc": os.cpu_count(), "python": sys.version.split()[0],
                 "wasmtime_py": version("wasmtime")},
        "tenants": args.tenants, "rounds": args.rounds, "logical_mib_each": 4.0,
        "summary": {"vre1e": rows}}, indent=1))
    print(f"run written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
