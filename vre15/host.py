#!/usr/bin/env python3
"""VRE-1.5 host: replays one trace in real time under one residency policy.

    host.py SPEC_JSON        (run by vre15.runner inside a memory cgroup)

All tenants share one engine and one compiled module (VRE-1E packing). The
host is one process and one thread. Each request is admitted (authority
check, the same for every policy), made resident if needed, and served as
one guest call. Latency runs from the request's scheduled time to its
completion, so queueing behind a slow restore counts.

Policies:
  native      every tenant live from the start; no demotion ever. The kernel
              pages under the cgroup limit (swap allowed). This is the
              OS-behaviour baseline.
  idle-lru    demote after `idle_s` without a request; when over the live
              cap, evict least recently used. Demotion writes a snapshot
              and fsyncs it.
  envelope    idle-lru plus what only the envelope knows: demote a tenant
              the moment its authority window ends, and evict in the order
              outside-authority, then batch, then interactive (LRU within
              each).
  predictive  envelope plus pre-materializing interactive tenants when an
              authority window opens, before the first request arrives.

Authority is enforced before any policy acts, so authority violations are 0
by construction for every policy. The run reports them anyway.

Integrity: every served request is one deterministic guest step. At the end
each tenant's final digest must equal a reference: the initial snapshot
restored fresh plus the same number of steps, with no demotion.
"""

import json
import os
import sys
import time

import wasmtime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vre1"))
import snapshot as snap  # noqa: E402

sys.path.insert(0, HERE)
import traces as tr  # noqa: E402


def rss_bytes():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return 0


def majflt():
    with open("/proc/self/stat") as f:
        return int(f.read().rsplit(")", 1)[1].split()[9])


class Host:
    def __init__(self, spec):
        self.spec = spec
        self.policy = spec["policy"]
        with open(spec["trace"]) as f:
            self.trace = json.load(f)
        self.env = self.trace["envelopes"]
        self.engine = wasmtime.Engine()
        self.module = wasmtime.Module.deserialize_file(self.engine, spec["cwasm"])
        with open(os.path.join(os.path.dirname(HERE), "vre1", "workloads", "stateful.wat"), "rb") as f:
            import hashlib
            self.msha = hashlib.sha256(f.read()).hexdigest()
        n = self.trace["tenants"]
        self.live = {}                                  # t -> (store, exports)
        self.path = {t: spec["initial"][t] for t in range(n)}
        self.last = {t: -1e9 for t in range(n)}
        self.steps = [0] * n
        self.restores = self.demotions = self.snapshot_bytes = 0
        self.restore_ms = []
        self.base_rss = rss_bytes()
        # Managed policies derive their live cap from the same budget the
        # cgroup enforces: 85% of it, less this process's own baseline,
        # divided by the per-tenant resident cost measured in VRE-1E.
        if self.policy == "native":
            self.cap = n
        else:
            self.cap = max(1, int((0.85 * spec["budget_bytes"] - self.base_rss) // spec["per_tenant_bytes"]))

    # -- residency mechanics --------------------------------------------------
    def materialize(self, t):
        t0 = time.perf_counter()
        with open(self.path[t], "rb") as f:
            blob = bytearray(os.fstat(f.fileno()).st_size)
            f.readinto(blob)
        header, runs = snap.decode(blob)
        store = wasmtime.Store(self.engine)
        x = wasmtime.Instance(store, self.module, []).exports(store)
        snap.restore_into(store, x, header, runs, self.msha)
        self.live[t] = (store, x)
        self.restores += 1
        self.restore_ms.append((time.perf_counter() - t0) * 1000)

    def demote(self, t):
        store, x = self.live.pop(t)
        blob = snap.snapshot(store, x, self.msha, "sparse")
        path = os.path.join(self.spec["workdir"], f"t{t}.live")
        snap.write_file(path, blob)
        self.path[t] = path
        self.demotions += 1
        self.snapshot_bytes += len(blob)

    def authorized_now(self, t, now):
        return tr.authorized(self.env[t], now)

    def victim(self, now):
        cands = list(self.live)
        if self.policy == "idle-lru":
            return min(cands, key=lambda t: self.last[t])
        def rank(t):
            outside = not self.authorized_now(t, now)
            batch = self.env[t]["class"] == "batch"
            return (0 if outside else 1 if batch else 2, self.last[t])
        return min(cands, key=rank)

    def make_room(self, now, protect=None):
        while len(self.live) >= self.cap:
            v = self.victim(now)
            if v == protect:
                break
            self.demote(v)

    def housekeeping(self, now):
        if self.policy == "native":
            return
        for t in list(self.live):
            idle = now - self.last[t] > self.spec["idle_s"]
            expired = self.policy in ("envelope", "predictive") and not self.authorized_now(t, now)
            if idle or expired:
                self.demote(t)
        if self.policy == "predictive":
            # Speculative: only into free capacity (never evicts to make
            # room), at most one per pass so it cannot stall real requests.
            # A first version that evicted and prefetched every opening
            # window at once caused a prefetch storm; see the README.
            self.pending.extend(self.opening.pop_due(now))
            while self.pending:
                t = self.pending.pop(0)
                if (t not in self.live and self.env[t]["class"] == "interactive"
                        and self.authorized_now(t, now)):
                    if len(self.live) < self.cap:
                        self.materialize(t)
                        self.last[t] = now
                    break

    # -- run -------------------------------------------------------------------
    def run(self):
        n = self.trace["tenants"]
        if self.policy == "native":
            for t in range(n):
                self.materialize(t)
        self.restore_ms.clear()
        self.restores = 0
        self.opening = Openings(self.env) if self.policy == "predictive" else None
        self.pending = []
        results, rss_samples, next_sample = [], [], 0.0
        swap0, maj0 = vmstat(), majflt()
        start = time.perf_counter()
        for at, t, _intended in self.trace["requests"]:
            while True:
                now = time.perf_counter() - start
                if now >= next_sample:
                    rss_samples.append(rss_bytes())
                    next_sample += 0.5
                self.housekeeping(now)
                wait = at - (time.perf_counter() - start)
                if wait <= 0:
                    break
                time.sleep(min(wait, 0.05))
            now = time.perf_counter() - start
            if not self.authorized_now(t, at):
                results.append([t, None, "denied", False])
                continue
            if t not in self.live:
                self.make_room(now, protect=t)
                self.materialize(t)
            store, x = self.live[t]
            x["step"](store, 1)
            self.steps[t] += 1
            done = time.perf_counter() - start
            self.last[t] = done
            results.append([t, round((done - at) * 1000, 3), self.env[t]["class"], True])
        wall = time.perf_counter() - start
        swap1, maj1 = vmstat(), majflt()
        failures = self.integrity()
        return {"results": results, "rss_samples": rss_samples, "wall_s": wall,
                "restores": self.restores, "restore_ms": self.restore_ms, "demotions": self.demotions,
                "snapshot_bytes_written": self.snapshot_bytes, "major_faults": maj1 - maj0,
                "swap_pages_in": swap1[0] - swap0[0], "swap_pages_out": swap1[1] - swap0[1],
                "base_rss": self.base_rss, "integrity_failures": failures,
                "authority_violations": sum(1 for (t, lat, cls, ok), (at, _, _) in
                                            zip(results, self.trace["requests"])
                                            if ok and not tr.authorized(self.env[t], at))}

    def integrity(self):
        failures = 0
        for t in range(self.trace["tenants"]):
            if t not in self.live:
                self.materialize(t)
            store, x = self.live.pop(t)
            got = x["digest"](store)
            ref_store = wasmtime.Store(self.engine)
            rx = wasmtime.Instance(ref_store, self.module, []).exports(ref_store)
            with open(self.spec["initial"][t], "rb") as f:
                header, runs = snap.decode(f.read())
            snap.restore_into(ref_store, rx, header, runs, self.msha)
            if self.steps[t]:
                rx["step"](ref_store, self.steps[t])
            failures += got != rx["digest"](ref_store)
        return failures


class Openings:
    """Window-open times, in order, for the predictive policy."""

    def __init__(self, envelopes):
        self.q = sorted((s, t) for t, e in enumerate(envelopes) for s, _ in e["windows"])
        self.i = 0

    def pop_due(self, now):
        due = []
        while self.i < len(self.q) and self.q[self.i][0] <= now:
            due.append(self.q[self.i][1])
            self.i += 1
        return due


def vmstat():
    vals = {}
    with open("/proc/vmstat") as f:
        for line in f:
            k, v = line.split()
            vals[k] = int(v)
    return vals.get("pswpin", 0), vals.get("pswpout", 0)


if __name__ == "__main__":
    spec = json.loads(sys.argv[1])
    out = Host(spec).run()
    with open(spec["out"], "w") as f:
        json.dump(out, f)
    print("DONE")
