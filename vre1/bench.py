#!/usr/bin/env python3
"""VRE-1A (baseline materialization) and VRE-1B (dormant population).

    sudo python -m vre1.bench --reps 10 --tenants 500

Needs root, only to drop the page cache before cold restores
(`echo 3 > /proc/sys/vm/drop_caches`).

VRE-1A: for each logical size x density, build one state, snapshot it in
each codec, then restore it in fresh processes, `--reps` times cold and
`--reps` times warm.
VRE-1B: snapshot `--tenants` distinct tenant states. For each population
size N, restore random tenants from the first N, cold.

Every restore is checked for integrity: it serves the next request and must
match what the original instance produced. Snapshots live in a temporary
directory and are deleted; results go to vre1/runs/<run_id>/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
PROBE = HERE / "probe.py"
SIZES_PAGES = (16, 128, 512)             # 1, 8, 32 MiB logical
DENSITIES = (50, 250, 1000)              # per mille of 4 KiB blocks holding data
POPULATIONS = (1, 10, 50, 100, 500)
SLO_P95_MS = 100.0                       # the brief's example restore_p95_target


def probe(mode: str, spec: dict) -> dict:
    out = subprocess.run([sys.executable, "-I", str(PROBE), mode, json.dumps(spec)],
                         capture_output=True, text=True, timeout=600, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def drop_caches() -> None:
    os.sync()
    pathlib.Path("/proc/sys/vm/drop_caches").write_text("3")


def restore(spec: dict, cold: bool) -> dict:
    p = subprocess.Popen([sys.executable, "-I", str(PROBE), "restore", json.dumps(spec)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.stdout.readline().strip() != "READY":
        p.kill()
        raise RuntimeError(p.stderr.read())
    if cold:
        drop_caches()
    out, err = p.communicate("go\n", timeout=600)
    if p.returncode != 0:
        raise RuntimeError(err)
    return json.loads(out.strip().splitlines()[-1])


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)


def stats(xs: list[float]) -> dict:
    return {"p50": pct(xs, .5), "p95": pct(xs, .95), "p99": pct(xs, .99), "max": round(max(xs), 3), "n": len(xs)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--tenants", type=int, default=500)
    ap.add_argument("--samples", type=int, default=40, help="restores per population size")
    ap.add_argument("--out", type=pathlib.Path, default=HERE / "runs")
    args = ap.parse_args()
    if os.geteuid() != 0:
        print("needs root to drop the page cache for cold restores", file=sys.stderr)
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / run_id
    out.mkdir(parents=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="vre1-"))
    samples = open(out / "samples.jsonl", "w")
    summary = {"vre1a": [], "vre1b": []}
    try:
        # ---- VRE-1A -----------------------------------------------------------
        for pages in SIZES_PAGES:
            for density in DENSITIES:
                d = work / f"p{pages}-d{density}"
                d.mkdir()
                made = probe("make", {"dir": str(d), "cwasm": str(d / "m.cwasm"), "pages": pages,
                                      "density": density, "seed": 7, "steps": 100})
                for codec, c in made["codecs"].items():
                    spec = {"path": c["path"], "cwasm": str(d / "m.cwasm"),
                            "digest_after_next": made["digest_after_next"],
                            "memory_sha256_after_next": made["memory_sha256_after_next"]}
                    for temp in ("cold", "warm"):
                        rs = []
                        for rep in range(args.reps):
                            r = restore(spec, cold=temp == "cold")
                            r.update(phase="vre1a", pages=pages, density=density, codec=codec,
                                     temperature=temp, rep=rep)
                            samples.write(json.dumps(r) + "\n")
                            rs.append(r)
                        ok = [r for r in rs if r["integrity_ok"]]
                        summary["vre1a"].append({
                            "logical_mib": made["logical_bytes"] / 2**20, "density_permille": density,
                            "codec": codec, "temperature": temp,
                            "hot_rss_mib": round(made["hot_rss_kib"] / 1024, 2),
                            "snapshot_bytes": c["bytes"],
                            "logical_to_snapshot_ratio": round(made["logical_bytes"] / c["bytes"], 2),
                            "snapshot_ms": round(c["snapshot_ms"], 2),
                            "snapshot_cpu_ms": round(c["cpu_ms"], 2),
                            "restore_ms": stats([r["restore_ms"] for r in ok]) if ok else None,
                            "restore_cpu_ms_p50": pct([r["restore_cpu_ms"] for r in ok], .5) if ok else None,
                            "phase_ms_p50": {k: pct([r[k] for r in ok], .5) for k in
                                             ("read_ms", "verify_ms", "decode_ms", "module_ms", "instantiate_ms", "write_ms")} if ok else None,
                            "first_request_us_p50": pct([r["first_request_us"] for r in ok], .5) if ok else None,
                            "rss_settled_mib_p50": round(pct([r["rss_settled_delta_kib"] for r in ok], .5) / 1024, 2) if ok else None,
                            "rss_peak_mib_p50": round(pct([r["rss_peak_delta_kib"] for r in ok], .5) / 1024, 2) if ok else None,
                            "integrity_failures": len(rs) - len(ok),
                            "module_compile_ms": round(made["compile_ms"], 3),
                            "module_deserialize_ms": round(made["deserialize_ms"], 3),
                        })
                        print(f"1A {pages:>4}p d{density:<4} {codec:<13} {temp}: "
                              f"p50={summary['vre1a'][-1]['restore_ms']['p50'] if ok else 'FAIL'} ms", flush=True)
                shutil.rmtree(d)

        # ---- VRE-1B -----------------------------------------------------------
        pop = work / "population"
        pop.mkdir()
        made = probe("make-many", {"dir": str(pop), "cwasm": str(pop / "m.cwasm"), "tenants": args.tenants,
                                   "pages": 64, "density": 250, "steps": 100, "codec": "sparse-zlib1"})
        tenants = made["tenants"]
        rng = random.Random(20261011)
        for n in [p for p in POPULATIONS if p <= args.tenants]:
            rs = []
            for s in range(args.samples):
                t = tenants[rng.randrange(n)]
                r = restore({"path": t["path"], "cwasm": str(pop / "m.cwasm"),
                             "digest_after_next": t["digest_after_next"],
                             "memory_sha256_after_next": t["memory_sha256_after_next"]}, cold=True)
                r.update(phase="vre1b", population=n, sample=s)
                samples.write(json.dumps(r) + "\n")
                rs.append(r)
            ok = [r for r in rs if r["integrity_ok"]]
            logical = n * made["logical_bytes_each"]
            peak_resident = max(r["rss_peak_delta_kib"] for r in rs) * 1024
            p95 = pct([r["restore_ms"] for r in ok], .95) if ok else None
            summary["vre1b"].append({
                "population": n,
                "logical_gib_entitled": round(logical / 2**30, 4),
                "dormant_bytes_on_disk": sum(t["bytes"] for t in tenants[:n]),
                "restore_ms_cold": stats([r["restore_ms"] for r in ok]) if ok else None,
                "integrity_failures": len(rs) - len(ok),
                "peak_resident_bytes_one_restore": peak_resident,
                "virtual_residency_ratio": round(logical / peak_resident, 2),
                "slo_p95_ms": SLO_P95_MS, "slo_met": p95 is not None and p95 <= SLO_P95_MS,
            })
            print(f"1B N={n:<4} p95={p95} ms  ratio={summary['vre1b'][-1]['virtual_residency_ratio']}", flush=True)
    finally:
        samples.close()
        shutil.rmtree(work, ignore_errors=True)

    from importlib.metadata import version
    manifest = {
        "run_id": run_id, "finished_at": datetime.now(timezone.utc).isoformat(),
        "host": {"kernel": platform.release(), "nproc": os.cpu_count(),
                 "mem_total_kib": int(next(ln.split()[1] for ln in open("/proc/meminfo") if ln.startswith("MemTotal"))),
                 "python": sys.version.split()[0], "wasmtime_py": version("wasmtime")},
        "mechanism": "Wasm instance snapshot (linear memory + globals) inside wasmtime; not a VM or process snapshot",
        "reps": args.reps, "tenants": args.tenants, "samples_per_population": args.samples,
        "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(HERE.glob("*.py")) + sorted((HERE / "workloads").glob("*.wat"))},
        "summary": summary,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"run written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
