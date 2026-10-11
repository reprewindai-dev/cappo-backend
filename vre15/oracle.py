#!/usr/bin/env python3
"""Offline bound: how much can the envelope save over recency, at best?

    python -m vre15.oracle [--seeds 201 ... ] [--variant informative]

Every VRE-1.5 experiment so far tested a mechanism. This measures the
ceiling instead: a page-granular cache model replayed over the same seeded
traces the experiments used, counting demand misses under four eviction
policies at several cache capacities. No timing, no kernel, no root.

Model (declared here, not fitted to any result):
- each tenant's hot set is HOT_PAGES pages: the guest's `step` rewrites one
  random word in the first 64 KiB of linear memory (16 x 4 KiB pages);
- each served request touches one of its tenant's hot pages, chosen
  uniformly by a seeded generator (the guest's own xorshift stream is not
  reproduced; only the uniform choice is);
- requests outside authority are denied and touch nothing;
- the cache holds `capacity` pages and starts empty for every policy.

Policies:
  lru        evict the least recently used page. The stand-in for the
             kernel's reclaim.
  envelope   evict a page of a tenant that is outside authority now,
             choosing the tenant whose next window opens furthest in the
             future (never-again first); with no such page, fall back to
             LRU. This uses only what the envelope knows: window times, not
             request times.
  envelope+prefetch
             envelope, plus at each window opening the tenant's whole hot
             set is fetched ahead of its first request (prefetch reads are
             counted separately, not as misses). A prefetch may evict only
             pages of tenants outside authority.
  opt        Belady: evict the page whose next use is furthest away, with
             full knowledge of every future request. No real policy can
             beat it; it is the ceiling.

Reported per capacity: misses for each policy, and the share of the
LRU-to-OPT gap the envelope closes:
    closed = (lru - envelope) / (lru - opt)
Near 0 means the envelope adds little a recency policy does not already
know on this trace; near 1 means it is as good as knowing the future.
"""

import argparse
import heapq
import json
import random
import sys
from collections import OrderedDict

from vre15 import traces

HOT_PAGES = 16


def served_refs(trace, seed):
    """[(time, tenant, page)] for every request inside authority."""
    rng = random.Random(seed * 1_000_003 + 17)
    env = trace["envelopes"]
    return [(at, t, rng.randrange(HOT_PAGES)) for at, t, _ in trace["requests"]
            if traces.authorized(env[t], at)]


def next_open(env, now):
    return min((s for s, _ in env["windows"] if s > now), default=float("inf"))


def run_lru(refs, capacity):
    cache, misses = OrderedDict(), 0
    for _, t, p in refs:
        k = (t, p)
        if k in cache:
            cache.move_to_end(k)
            continue
        misses += 1
        if len(cache) >= capacity:
            cache.popitem(last=False)
        cache[k] = None
    return misses


class EnvelopeCache:
    """Page LRU plus per-tenant page sets, for envelope victim choice."""

    def __init__(self, envs, capacity):
        self.envs, self.capacity = envs, capacity
        self.lru = OrderedDict()
        self.by_tenant = {}

    def __contains__(self, k):
        return k in self.lru

    def touch(self, k):
        self.lru.move_to_end(k)

    def victim(self, now, outside_only=False):
        best, best_key = None, None
        for t, pages in self.by_tenant.items():
            if pages and not traces.authorized(self.envs[t], now):
                key = next_open(self.envs[t], now)
                if best_key is None or key > best_key:
                    best, best_key = t, key
        if best is not None:
            # the tenant's least recently used page
            return next(k for k in self.lru if k[0] == best)
        if outside_only:
            return None
        return next(iter(self.lru))

    def insert(self, k, now, outside_only=False):
        if len(self.lru) >= self.capacity:
            v = self.victim(now, outside_only)
            if v is None:
                return False
            del self.lru[v]
            self.by_tenant[v[0]].discard(v)
        self.lru[k] = None
        self.by_tenant.setdefault(k[0], set()).add(k)
        return True


def run_envelope(refs, capacity, envs, prefetch=False):
    cache, misses, prefetched = EnvelopeCache(envs, capacity), 0, 0
    opens = sorted((s, t) for t, e in enumerate(envs) for s, _ in e["windows"]) if prefetch else []
    i = 0
    for now, t, p in refs:
        while i < len(opens) and opens[i][0] <= now:
            s, u = opens[i]
            i += 1
            for q in range(HOT_PAGES):
                k = (u, q)
                if k in cache:
                    continue
                if not cache.insert(k, s, outside_only=True):
                    break
                prefetched += 1
        k = (t, p)
        if k in cache:
            cache.touch(k)
            continue
        misses += 1
        cache.insert(k, now)
    return misses, prefetched


def run_opt(refs, capacity):
    keys = [(t, p) for _, t, p in refs]
    nxt, last = [0] * len(keys), {}
    for i in range(len(keys) - 1, -1, -1):
        nxt[i] = last.get(keys[i], float("inf"))
        last[keys[i]] = i
    cache, heap, misses = {}, [], 0                 # cache: key -> its next use
    for i, k in enumerate(keys):
        if k not in cache:
            misses += 1
            if len(cache) >= capacity:
                while True:
                    neg, v = heapq.heappop(heap)
                    if v in cache and cache[v] == -neg:
                        del cache[v]
                        break
        cache[k] = nxt[i]
        heapq.heappush(heap, (-nxt[i], k))
    return misses


def bound(variant, seed, tenants=300, duration=90, rate=250, fractions=(0.1, 0.25, 0.5, 0.75)):
    trace = traces.make(variant, tenants, duration, rate, seed)
    refs = served_refs(trace, seed)
    envs = trace["envelopes"]
    total = tenants * HOT_PAGES
    out = []
    for f in fractions:
        cap = max(1, int(total * f))
        lru = run_lru(refs, cap)
        env, _ = run_envelope(refs, cap, envs)
        envp, pre = run_envelope(refs, cap, envs, prefetch=True)
        opt = run_opt(refs, cap)
        gap = lru - opt
        out.append({"variant": variant, "seed": seed, "capacity_fraction": f, "capacity_pages": cap,
                    "refs": len(refs), "lru": lru, "envelope": env, "envelope_prefetch": envp,
                    "prefetch_reads": pre, "opt": opt,
                    "closed_by_envelope": round((lru - env) / gap, 3) if gap else None,
                    "closed_by_envelope_prefetch": round((lru - envp) / gap, 3) if gap else None})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[201, 202, 203, 204, 205])
    ap.add_argument("--variant", default="informative")
    ap.add_argument("--fractions", type=float, nargs="+", default=[0.1, 0.25, 0.5, 0.75])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = [r for s in a.seeds for r in bound(a.variant, s, fractions=tuple(a.fractions))]
    if a.json:
        json.dump(rows, sys.stdout, indent=1)
        return
    for r in rows:
        print(f"{r['variant']:<13} s{r['seed']:<4} cap {r['capacity_fraction']:>4} ({r['capacity_pages']:>4} pages) "
              f"refs {r['refs']}  lru {r['lru']:>6}  env {r['envelope']:>6}  env+pf {r['envelope_prefetch']:>6} "
              f"(+{r['prefetch_reads']} pf)  opt {r['opt']:>6}  closed {r['closed_by_envelope']} / "
              f"{r['closed_by_envelope_prefetch']}")


if __name__ == "__main__":
    main()
