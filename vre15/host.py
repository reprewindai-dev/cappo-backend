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
  native-steered
              native (every tenant live, kernel paging, swap allowed), plus
              the envelope steering the kernel's own page-granular mechanism:
              madvise(MADV_PAGEOUT) over a tenant's linear memory when its
              authority window closes, madvise(MADV_WILLNEED) when one opens.
  native-pageout
              native-steered without the window-open WILLNEED: only
              madvise(MADV_PAGEOUT) when a window closes. Isolates the cost of
              the t = 0 WILLNEED burst seen on the control trace.
  envelope-headroom
              native, plus a background reclaimer thread that keeps a free
              band below the cgroup limit, so that request-path faults find
              free memory instead of reclaiming synchronously. On cgroup v1
              with the classic LRU, a faulting task that reclaims can wait,
              with no timeout, for swap writeback it queued itself
              (folio_wait_writeback in shrink_folio_list); that is the
              suspected source of native's 200-900 ms stalls. The thread
              pages out whole tenants with MADV_PAGEOUT, choosing victims by
              the envelope: outside authority first, furthest next window
              opening first (Belady with known windows); then LRU.
  lru-headroom
              the same background reclaimer, choosing victims by recency
              alone (least recently requested first). The control: it has
              background reclaim but no envelope, so any gap between it and
              envelope-headroom is the envelope's contribution.

Authority is enforced before any policy acts, so authority violations are 0
by construction for every policy. The run reports them anyway.

Integrity: every served request is one deterministic guest step. At the end
each tenant's final digest must equal a reference: the initial snapshot
restored fresh plus the same number of steps, with no demotion.
"""

import ctypes
import json
import os
import sys
import threading
import time

import wasmtime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vre1"))
import snapshot as snap  # noqa: E402

sys.path.insert(0, HERE)
import traces as tr  # noqa: E402

MADV_WILLNEED = 3
MADV_PAGEOUT = 21                       # Linux 5.4+
KERNEL_PAGED = ("native", "native-steered", "native-pageout", "envelope-headroom", "lru-headroom")
STEERED = ("native-steered", "native-pageout")
HEADROOM = ("envelope-headroom", "lru-headroom")
_libc = ctypes.CDLL(None, use_errno=True)
_libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]


def advise(store, exports, advice):
    """madvise over a tenant's whole linear memory; returns errno (0 = ok)."""
    mem = exports["memory"]
    base = ctypes.addressof(mem.data_ptr(store).contents)
    if _libc.madvise(base, mem.data_len(store), advice) != 0:
        return ctypes.get_errno()
    return 0


def memcg_usage_path():
    for line in open("/proc/self/cgroup").read().splitlines():
        _, ctrls, path = line.split(":", 2)
        if "memory" in ctrls.split(","):
            return os.path.join("/sys/fs/cgroup/memory", path.lstrip("/"), "memory.usage_in_bytes")
    return None


def memcg_bytes(path):
    """What the cgroup charges (anon, page cache and swap cache), which is
    what the budget limits. RSS alone misses pages held in swap cache."""
    try:
        with open(path) as f:
            return int(f.read())
    except (OSError, TypeError, ValueError):
        return 0


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
        self.region = {}                                # t -> (base address, length) of linear memory
        self.path = {t: spec["initial"][t] for t in range(n)}
        self.last = {t: -1e9 for t in range(n)}
        self.steps = [0] * n
        self.restores = self.demotions = self.snapshot_bytes = 0
        self.restore_ms = []
        self.base_rss = rss_bytes()
        # Managed policies derive their live cap from the same budget the
        # cgroup enforces: 85% of it, less this process's own baseline,
        # divided by the per-tenant resident cost measured in VRE-1E.
        if self.policy in KERNEL_PAGED:
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
        mem = x["memory"]
        self.region[t] = (ctypes.addressof(mem.data_ptr(store).contents), mem.data_len(store))
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

    def housekeeping(self, now, horizon):
        """`horizon` is the trace time of the oldest request still waiting
        (or now, if none is waiting). Idle and expiry are judged at it, never
        at wall-clock time: a host that has fallen behind must not demote a
        tenant whose queued requests were authorized when they arrived. Run
        20261011T053146Z judged expiry at wall-clock time and thrashed."""
        if self.policy == "native" or self.policy in HEADROOM:
            return                                      # kernel-paged; the reclaimer runs on its own thread
        if self.policy in STEERED:
            for t, kind in self.edges.pop_due(horizon):
                if kind == "open" and self.policy == "native-pageout":
                    continue
                store, x = self.live[t]
                err = advise(store, x, MADV_PAGEOUT if kind == "close" else MADV_WILLNEED)
                self.advice[kind] += 1
                self.advice_errors += err != 0
            return
        for t in list(self.live):
            idle = horizon - self.last[t] > self.spec["idle_s"]
            expired = self.policy in ("envelope", "predictive") and not self.authorized_now(t, horizon)
            if idle or expired:
                self.demote(t)
        if self.policy == "predictive":
            # Speculative: only into free capacity (never evicts to make
            # room), at most one per pass so it cannot stall real requests.
            # A first version that evicted and prefetched every opening
            # window at once caused a prefetch storm; see the README.
            self.pending.extend(self.opening.pop_due(horizon))
            while self.pending:
                t = self.pending.pop(0)
                if (t not in self.live and self.env[t]["class"] == "interactive"
                        and self.authorized_now(t, horizon)):
                    if len(self.live) < self.cap:
                        self.materialize(t)
                        self.last[t] = horizon
                    break

    # -- run -------------------------------------------------------------------
    def run(self):
        n = self.trace["tenants"]
        self.reclaimer = None
        if self.policy in HEADROOM:
            # Started before warm-up: a real deployment would run it always.
            budget = self.spec["budget_bytes"]
            band = self.spec.get("headroom_band_bytes") or max(2 << 20, budget // 8)
            self.reclaimer = Reclaimer(self, budget, band)
            self.reclaimer.start()
        warm_swap0 = vmstat()
        if self.policy in KERNEL_PAGED:
            for t in range(n):
                self.materialize(t)
        warm_swap1 = vmstat()
        self.edges = Edges(self.env) if self.policy in STEERED else None
        self.advice, self.advice_errors = {"open": 0, "close": 0}, 0
        self.restore_ms.clear()
        self.restores = 0
        self.opening = Openings(self.env) if self.policy == "predictive" else None
        self.pending = []
        results, rss_samples, cg_samples, next_sample = [], [], [], 0.0
        cg_path = memcg_usage_path()
        swap0, maj0 = vmstat(), majflt()
        start = time.perf_counter()
        if self.reclaimer:
            self.reclaimer.t0 = start
        for at, t, _intended in self.trace["requests"]:
            while True:
                now = time.perf_counter() - start
                if now >= next_sample:
                    rss_samples.append(rss_bytes())
                    cg_samples.append(memcg_bytes(cg_path))
                    next_sample += 0.5
                self.housekeeping(now, min(now, at))
                wait = at - (time.perf_counter() - start)
                if wait <= 0:
                    break
                time.sleep(min(wait, 0.05))
            now = time.perf_counter() - start
            if not self.authorized_now(t, at):
                results.append([t, None, "denied", False])
                continue
            if t not in self.live:
                self.make_room(at, protect=t)
                self.materialize(t)
            store, x = self.live[t]
            if self.reclaimer:
                self.reclaimer.paged.discard(t)
            x["step"](store, 1)
            self.steps[t] += 1
            done = time.perf_counter() - start
            self.last[t] = max(at, self.last[t])
            results.append([t, round((done - at) * 1000, 3), self.env[t]["class"], True])
        wall = time.perf_counter() - start
        swap1, maj1 = vmstat(), majflt()
        reclaim = None
        if self.reclaimer:
            self.reclaimer.halt.set()
            self.reclaimer.join()
            reclaim = self.reclaimer.stats()
        failures = self.integrity()
        return {"results": results, "rss_samples": rss_samples, "memcg_samples": cg_samples, "wall_s": wall,
                "restores": self.restores, "restore_ms": self.restore_ms, "demotions": self.demotions,
                "snapshot_bytes_written": self.snapshot_bytes, "major_faults": maj1 - maj0,
                "swap_pages_in": swap1[0] - swap0[0], "swap_pages_out": swap1[1] - swap0[1],
                "warmup_swap_pages_out": warm_swap1[1] - warm_swap0[1],
                "advice": self.advice, "advice_errors": self.advice_errors, "reclaim": reclaim,
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


class Reclaimer(threading.Thread):
    """Background headroom keeper for the *-headroom policies.

    Every `period` seconds it reads the cgroup's charge. Above `high` (limit
    minus one band) it pages out whole tenants with MADV_PAGEOUT, in victim
    order, until the charge is at or below `low` (limit minus two bands). The
    reclaim and its swap writeback happen on this thread; ctypes releases
    the GIL during madvise, so request handling continues meanwhile.

    Both victim orders share two safety rules, so they differ only in the
    order itself:
    - a tenant requested within the last `protect_s` seconds of trace time
      is never a victim (the first version had no such rule and paged out
      the hot set thousands of times per run, because the process's
      non-tenant memory alone sits near the low mark);
    - at most `per_pass` tenants are paged out per pass, so background
      writeback is paced rather than bursty.
    A tenant paged out once is skipped until it is requested again.
    """

    def __init__(self, host, limit, band, period=0.005, protect_s=2.0, per_pass=4):
        super().__init__(daemon=True)
        self.h, self.high, self.low, self.period = host, limit - band, limit - 2 * band, period
        self.band, self.protect_s, self.per_pass = band, protect_s, per_pass
        # Diagnostic levels: 1 = choose victims but page out nothing;
        # 2 = only read the cgroup charge; 3 = only sleep.
        self.dry_run = int(host.spec.get("reclaim_dry_run") or 0)
        self.path = memcg_usage_path()
        self.halt = threading.Event()
        self.paged = set()
        self.t0 = None                                  # trace start, set when the trace begins
        self.passes = self.pageouts = self.errors = 0
        self.pass_ms = []

    def now(self):
        return 0.0 if self.t0 is None else time.perf_counter() - self.t0

    def order(self):
        h, now = self.h, self.now()
        cands = [t for t in list(h.region) if t not in self.paged and now - h.last[t] > self.protect_s]
        if h.policy == "lru-headroom":
            return sorted(cands, key=lambda t: h.last[t])

        def key(t):
            env = h.env[t]
            if not tr.authorized(env, now):
                nxt = min((s for s, _ in env["windows"] if s > now), default=float("inf"))
                return (0, -nxt, h.last[t])
            return (1, 0, h.last[t])
        return sorted(cands, key=key)

    def run(self):
        while not self.halt.wait(self.period):
            if self.dry_run == 3:
                continue
            if memcg_bytes(self.path) <= self.high or self.dry_run == 2:
                continue
            t0 = time.perf_counter()
            self.passes += 1
            for t in self.order()[:self.per_pass]:
                base, length = self.h.region[t]
                if self.dry_run:
                    pass
                elif _libc.madvise(base, length, MADV_PAGEOUT) != 0:
                    self.errors += 1
                self.paged.add(t)
                self.pageouts += 1
                if memcg_bytes(self.path) <= self.low:
                    break
            self.pass_ms.append((time.perf_counter() - t0) * 1000)

    def stats(self):
        ms = sorted(self.pass_ms)

        def q(f):
            return round(ms[min(len(ms) - 1, int(f * len(ms)))], 3) if ms else None
        return {"band_bytes": self.band, "protect_s": self.protect_s, "per_pass": self.per_pass,
                "passes": self.passes, "pageouts": self.pageouts,
                "errors": self.errors, "pass_ms_p50": q(.5), "pass_ms_p99": q(.99),
                "pass_ms_max": round(ms[-1], 3) if ms else None}


class Edges:
    """Authority window opens and closes, in time order, for the steered policies."""

    def __init__(self, envelopes):
        self.q = sorted([(s, t, "open") for t, e in enumerate(envelopes) for s, _ in e["windows"]]
                        + [(e_, t, "close") for t, e in enumerate(envelopes) for _, e_ in e["windows"]])
        self.i = 0

    def pop_due(self, now):
        due = []
        while self.i < len(self.q) and self.q[self.i][0] <= now:
            due.append((self.q[self.i][1], self.q[self.i][2]))
            self.i += 1
        return due


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
