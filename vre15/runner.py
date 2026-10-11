#!/usr/bin/env python3
"""VRE-1.5 runner: every policy, the same trace, the same memory budget.

    sudo python -m vre15.runner --tenants 300 --duration 90 --budget-mib 160

For each trace variant (informative, uninformative) and each policy (native,
idle-lru, envelope, predictive):
  - drops the page cache;
  - creates a cgroup v1 memory group with limit = budget. native may swap
    (memsw unlimited, a swap file is switched on for its runs). The managed
    policies may not: memsw = budget, so exceeding it is an OOM kill, not
    hidden swapping;
  - runs vre15/host.py inside it and collects its results.

Reported per run (the brief's VRE-1.5 metrics):
- requests served, and the share within the class deadline (interactive
  100 ms, batch 1000 ms);
- latency p50, p95 and p99 per class;
- denied requests and authority violations (must be 0);
- mean and peak resident memory;
- useful work per GiB: requests served within deadline per mean resident
  GiB;
- restores, restore p95, demotions and snapshot bytes written;
- swap pages in and out, major faults;
- integrity failures (must be 0) and OOM kills.

Results go to vre15/runs/<run_id>/.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from vre1.bench import probe
from vre15 import traces

HERE = pathlib.Path(__file__).resolve().parent
POLICIES = ("native", "idle-lru", "envelope", "predictive", "native-steered", "native-pageout",
            "envelope-headroom", "lru-headroom", "envelope-prefetch", "envelope-declared",
            "lru-pageout", "native-cold", "pageout-delay", "pageout-bg")
KERNEL_PAGED = ("native", "native-steered", "native-pageout", "envelope-headroom", "lru-headroom",
                "envelope-prefetch", "envelope-declared", "lru-pageout", "native-cold", "pageout-delay",
                "pageout-bg")
PER_TENANT_BYTES = 1_200_000            # VRE-1E measured 1.10 MB; small margin


def own_memcg() -> pathlib.Path:
    rel = next(line.split(":", 2)[2] for line in open("/proc/self/cgroup").read().splitlines()
               if line.split(":")[1] == "memory")
    return pathlib.Path("/sys/fs/cgroup/memory") / rel.lstrip("/")


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3) if xs else None


def stall_stats(results, trace, deadline_ms=traces.DEADLINE_MS["interactive"]):
    """Stall seconds: 1 s buckets of trace time in which any served
    interactive request exceeded its deadline. Episodes: maximal runs of
    consecutive stall seconds. Results and trace requests align by index."""
    buckets = sorted({int(req[0]) for r, req in zip(results, trace["requests"])
                      if r[3] and r[2] == "interactive" and r[1] > deadline_ms})
    episodes = sum(1 for i, b in enumerate(buckets) if i == 0 or buckets[i - 1] != b - 1)
    return {"stall_seconds": len(buckets), "stall_episodes": episodes, "stall_buckets": buckets}


def summarize(raw: dict, trace: dict, policy: str, oom_kills: int, exit_code: int) -> dict:
    served = [r for r in raw.get("results", []) if r[3]]
    by = {}
    for cls, deadline in traces.DEADLINE_MS.items():
        lat = [r[1] for r in served if r[2] == cls]
        if lat:
            by[cls] = {"served": len(lat), "within_deadline": sum(1 for x in lat if x <= deadline),
                       "within_deadline_pct": round(100 * sum(1 for x in lat if x <= deadline) / len(lat), 2),
                       "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99)}
    rss = raw.get("rss_samples", [0])
    mean_res = sum(rss) / len(rss)
    within = sum(c["within_deadline"] for c in by.values())
    return {
        "policy": policy, "variant": trace["variant"], "exit_code": exit_code, "oom_kills": oom_kills,
        "requests": len(trace["requests"]), "served": len(served),
        "denied": sum(1 for r in raw.get("results", []) if r[2] == "denied"),
        "authority_violations": raw.get("authority_violations"),
        "integrity_failures": raw.get("integrity_failures"),
        "by_class": by,
        "mean_resident_mib": round(mean_res / 2**20, 1), "peak_resident_mib": round(max(rss) / 2**20, 1),
        "mean_memcg_mib": round(sum(cg) / len(cg) / 2**20, 1) if (cg := raw.get("memcg_samples")) else None,
        "peak_memcg_mib": round(max(cg) / 2**20, 1) if (cg := raw.get("memcg_samples")) else None,
        "useful_work_per_gib": round(within / (mean_res / 2**30), 1) if mean_res else None,
        "restores": raw.get("restores"), "restore_p95_ms": pct(raw.get("restore_ms", []), .95),
        "demotions": raw.get("demotions"), "snapshot_bytes_written": raw.get("snapshot_bytes_written"),
        "swap_pages_in": raw.get("swap_pages_in"), "swap_pages_out": raw.get("swap_pages_out"),
        "warmup_swap_pages_out": raw.get("warmup_swap_pages_out"),
        "advice": raw.get("advice"), "advice_errors": raw.get("advice_errors"), "reclaim": raw.get("reclaim"), "prefetch": raw.get("prefetch"),
        "major_faults": raw.get("major_faults"),
        "request_thread_major_faults": raw.get("request_thread_major_faults"),
        "recording": raw.get("recording"), "revoked_residency": raw.get("revoked_residency"),
        "release": raw.get("release"), "trace_start_wall": raw.get("trace_start_wall"),
        **(stall_stats(raw["results"], trace) if raw.get("results") else {}), "wall_s": round(raw.get("wall_s", 0), 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenants", type=int, default=300)
    ap.add_argument("--duration", type=float, default=90)
    ap.add_argument("--rate", type=float, default=250, help="mean requests per second")
    ap.add_argument("--budget-mib", type=int, nargs="+", default=[160])
    ap.add_argument("--idle-s", type=float, default=5.0)
    ap.add_argument("--headroom-kib", type=int, default=0,
                    help="background reclaimer band for the *-headroom policies (0 = host default)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run every (budget, seed, variant) cell this many times (A/A repeats)")
    ap.add_argument("--sample", action="store_true",
                    help="run vre15/sampler.py beside each run (PSI, vmstat, memcg failcnt every 100 ms)")
    ap.add_argument("--page-cluster", type=int, default=None,
                    help="set vm.page-cluster (swap readahead, 2^N pages) for the whole run, restored after")
    ap.add_argument("--probe-revoked", action="store_true",
                    help="every policy measures resident pages of tenants 0/1/5 s after authority ends")
    ap.add_argument("--record-ws", action="store_true",
                    help="every policy runs the working-set recording phase after warm-up "
                         "(envelope-declared requires it)")
    ap.add_argument("--reclaim-dry-run", type=int, default=0,
                    help="diagnostic: 1 = reclaimer chooses victims but pages nothing out; "
                         "2 = only reads the cgroup charge; 3 = only sleeps")
    ap.add_argument("--seed", type=int, nargs="+", default=[7],
                    help="trace seeds; several give paired repeats of every cell")
    ap.add_argument("--variants", nargs="*", default=["informative", "uninformative"])
    ap.add_argument("--policies", nargs="*", default=list(POLICIES))
    ap.add_argument("--rotate-order", action="store_true",
                    help="rotate the policy order from one cell to the next, so no policy always runs first")
    ap.add_argument("--out", type=pathlib.Path, default=HERE / "runs")
    args = ap.parse_args()
    if os.geteuid() != 0:
        print("needs root (cgroups, swap, page cache)", file=sys.stderr)
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / run_id
    out.mkdir(parents=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="vre15-"))
    swapfile = work / "swapfile"
    rows = []
    pc_path = pathlib.Path("/proc/sys/vm/page-cluster")
    pc_saved = pc_path.read_text().strip()
    if args.page_cluster is not None:
        pc_path.write_text(str(args.page_cluster))
    try:
        made = probe("make-many", {"dir": str(work), "cwasm": str(work / "m.cwasm"), "tenants": args.tenants,
                                   "pages": 64, "density": 250, "steps": 0, "codec": "sparse"})
        initial = [t["path"] for t in made["tenants"]]
        subprocess.run(["fallocate", "-l", "2G", str(swapfile)], check=True)
        os.chmod(swapfile, 0o600)
        subprocess.run(["mkswap", str(swapfile)], check=True, capture_output=True)
        cells = [(b, v, s, r) for b in args.budget_mib for s in args.seed for r in range(args.repeat)
                 for v in args.variants]
        for i, (budget_mib, variant, seed, rep) in enumerate(cells):
            budget = budget_mib * 2**20
            trace = traces.make(variant, args.tenants, args.duration, args.rate, seed)
            tag = (f"{budget_mib}-{variant}" + (f"-s{seed}" if len(args.seed) > 1 else "")
                   + (f"-r{rep}" if args.repeat > 1 else ""))
            tpath = work / f"trace-{variant}-{seed}.json"
            tpath.write_text(json.dumps(trace))
            k = i % len(args.policies) if args.rotate_order else 0
            for pos, policy in enumerate(args.policies[k:] + args.policies[:k]):
                cg = own_memcg() / f"vre15-{run_id}-{tag}-{policy}"
                cg.mkdir()
                (cg / "memory.limit_in_bytes").write_text(str(budget))
                kernel_paged = policy in KERNEL_PAGED
                if not kernel_paged:
                    (cg / "memory.memsw.limit_in_bytes").write_text(str(budget))
                rundir = work / f"{tag}-{policy}"
                rundir.mkdir()
                if kernel_paged:
                    subprocess.run(["swapon", str(swapfile)], check=True)
                os.sync()
                pathlib.Path("/proc/sys/vm/drop_caches").write_text("3")
                spec = {"policy": policy, "trace": str(tpath), "cwasm": str(work / "m.cwasm"),
                        "initial": initial, "workdir": str(rundir), "idle_s": args.idle_s,
                        "budget_bytes": budget, "per_tenant_bytes": PER_TENANT_BYTES,
                        "headroom_band_bytes": args.headroom_kib * 1024, "reclaim_dry_run": args.reclaim_dry_run,
                        "record_ws": args.record_ws, "probe_revoked": args.probe_revoked,
                        "out": str(rundir / "result.json")}
                sampler = None
                if args.sample:
                    sampler = subprocess.Popen([sys.executable, "-I", str(HERE / "sampler.py"),
                                                str(out / f"{tag}-{policy}.samples.jsonl"), str(cg)])
                proc = subprocess.run(
                    [sys.executable, "-I", str(HERE / "host.py"), json.dumps(spec)],
                    preexec_fn=lambda cg=cg: (cg / "cgroup.procs").write_text(str(os.getpid())),
                    capture_output=True, text=True, timeout=args.duration * 6 + 600)
                if sampler:
                    sampler.terminate()
                    sampler.wait(timeout=10)
                oom = next(int(line.split()[1]) for line in (cg / "memory.oom_control").read_text().splitlines()
                           if line.startswith("oom_kill "))
                if kernel_paged:
                    subprocess.run(["swapoff", str(swapfile)], check=True)
                raw = json.loads((rundir / "result.json").read_text()) if (rundir / "result.json").exists() else {}
                row = summarize(raw, trace, policy, oom, proc.returncode)
                row["budget_mib"], row["seed"], row["order_in_cell"] = budget_mib, seed, pos
                if args.repeat > 1:
                    row["rep"] = rep
                if proc.returncode != 0:
                    row["stderr_tail"] = proc.stderr[-600:]
                rows.append(row)
                (out / f"{tag}-{policy}.json").write_text(json.dumps(raw.get("results", [])))
                cg.rmdir()
                shutil.rmtree(rundir, ignore_errors=True)
                ic = row["by_class"].get("interactive", {})
                print(f"{budget_mib:>4}MiB s{seed:<3} {variant:<13} {policy:<14} exit={proc.returncode} oom={oom} served={row['served']} "
                      f"interactive<=100ms={ic.get('within_deadline_pct')}% p99={ic.get('p99_ms')} "
                      f"mean_res={row['mean_resident_mib']}MiB memcg={row['mean_memcg_mib']}MiB work/GiB={row['useful_work_per_gib']} "
                      f"swapin={row['swap_pages_in']} integrity_fail={row['integrity_failures']}", flush=True)
    finally:
        subprocess.run(["swapoff", str(swapfile)], capture_output=True)
        pc_path.write_text(pc_saved)
        shutil.rmtree(work, ignore_errors=True)
    from importlib.metadata import version
    (out / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "finished_at": datetime.now(timezone.utc).isoformat(),
        "host": {"kernel": platform.release(), "nproc": os.cpu_count(), "python": sys.version.split()[0],
                 "wasmtime_py": version("wasmtime")},
        "params": vars(args) | {"out": str(args.out), "per_tenant_bytes": PER_TENANT_BYTES,
                                "page_cluster_in_effect": args.page_cluster if args.page_cluster is not None
                                else int(pc_saved)},
        "summary": {"vre15": rows}}, indent=1))
    print(f"run written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
