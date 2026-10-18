"""Request traces for VRE-1.5, generated from a fixed, seeded model.

The model is declared here, before any policy is measured, so no policy can
be tuned to a trace after the fact.

Each tenant has an envelope:
  class     "interactive" (deadline 100 ms) or "batch" (deadline 1000 ms)
  windows   authority windows [start, end) in seconds; outside them every
            request for the tenant is denied, by every policy
Requests arrive only inside windows, as a Poisson process whose rate
follows a Zipf popularity over tenants (s = 1.1). A further 5% of
requests are aimed outside any window (expired or not-yet-valid authority)
and must be denied.

Two trace variants:
  "informative"    40% interactive, 60% batch; each tenant gets 1-3 short
                   sessions (10-30 s) at random times. The envelope carries
                   real information about when a tenant can be used.
  "uninformative"  the control. Every tenant is interactive with one window
                   covering the whole run, so the envelope says nothing a
                   recency policy does not already know. On it,
                   envelope-aware policies must not beat idle-LRU; if they
                   do, the experiment is broken.
"""

from __future__ import annotations

import random

DEADLINE_MS = {"interactive": 100.0, "batch": 1000.0}


def make(variant: str, tenants: int, duration_s: float, total_rate: float, seed: int) -> dict:
    rng = random.Random(seed)
    weights = [1.0 / (i + 1) ** 1.1 for i in range(tenants)]
    rng.shuffle(weights)
    norm = sum(weights)
    envelopes = []
    for _ in range(tenants):
        if variant == "uninformative":
            envelopes.append({"class": "interactive", "windows": [[0.0, duration_s]]})
            continue
        cls = "interactive" if rng.random() < 0.4 else "batch"
        windows = []
        for _ in range(rng.randint(1, 3)):
            length = rng.uniform(10, 30)
            start = rng.uniform(0, max(0.0, duration_s - length))
            windows.append([round(start, 3), round(start + length, 3)])
        windows.sort()
        envelopes.append({"class": cls, "windows": windows})
    requests = []
    for t, (w, env) in enumerate(zip(weights, envelopes)):
        # Average rate over the whole run, concentrated into the windows.
        active = sum(e - s for s, e in env["windows"])
        inst = total_rate * (w / norm) * duration_s / active
        for start, end in env["windows"]:
            at = start + rng.expovariate(inst)
            while at < end:
                requests.append([round(at, 4), t, True])
                at += rng.expovariate(inst)
    for _ in range(int(0.05 * len(requests))):         # aimed outside authority
        t = rng.randrange(tenants)
        at = rng.uniform(0, duration_s)
        if not any(s <= at < e for s, e in envelopes[t]["windows"]):
            requests.append([round(at, 4), t, False])
    requests.sort()
    return {"variant": variant, "tenants": tenants, "duration_s": duration_s, "seed": seed,
            "envelopes": envelopes, "requests": requests}


def authorized(envelope: dict, at: float) -> bool:
    return any(s <= at < e for s, e in envelope["windows"])
