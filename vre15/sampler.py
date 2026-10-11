#!/usr/bin/env python3
"""Sidecar sampler for VRE-1.5 runs (runs outside the measured cgroup).

    sampler.py OUT_JSONL CGROUP_DIR [INTERVAL_S]

Every INTERVAL_S (default 0.1 s) appends one JSON line:
    [wall_time, psi_some_us, psi_full_us, nr_writeback, pswpin, pswpout,
     pgmajfault, memcg_failcnt, memcg_usage_bytes]
until it receives SIGTERM.

- psi_*: system-wide memory pressure stall totals (/proc/pressure/memory,
  microseconds). They include memcg-limit stalls; this host's global
  allocstall/pgsteal_direct counters do not move during cgroup v1 limit
  reclaim, so PSI is the stall signal.
- memcg_failcnt: how many times the cgroup hit its limit
  (memory.failcnt, cgroup v1).
Used to test H-mech (native's collapses are request threads waiting on
swap writes during reclaim) by lining stall seconds up with these
counters, without kernel tracing. Standard library only.
"""

import json
import signal
import sys
import time

FIELDS = ("nr_writeback", "pswpin", "pswpout", "pgmajfault")


def psi():
    some = full = None
    with open("/proc/pressure/memory") as f:
        for line in f:
            kind, *kv = line.split()
            total = int(dict(x.split("=") for x in kv)["total"])
            if kind == "some":
                some = total
            elif kind == "full":
                full = total
    return some, full


def vmstat():
    vals = {}
    with open("/proc/vmstat") as f:
        for line in f:
            k, v = line.split()
            if k in FIELDS:
                vals[k] = int(v)
    return [vals.get(k, 0) for k in FIELDS]


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read())
    except (OSError, ValueError):
        return None


def main():
    out, cg = sys.argv[1], sys.argv[2]
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 0.1
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    with open(out, "w") as f:
        nxt = time.time()
        while not stop:
            some, full = psi()
            row = [round(time.time(), 4), some, full, *vmstat(),
                   read_int(f"{cg}/memory.failcnt"), read_int(f"{cg}/memory.usage_in_bytes")]
            f.write(json.dumps(row) + "\n")
            nxt += interval
            time.sleep(max(0.0, nxt - time.time()))


if __name__ == "__main__":
    main()
