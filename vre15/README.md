# VRE-1.5: envelope-aware residency vs native OS behaviour

The brief's falsifier: *"If VRE-aware residency control cannot materially
improve a defined metric … over native OS behavior at the same
physical-resource budget, then the performance thesis fails."*

**Result: on this experiment the performance thesis fails.** Native kernel
paging met every deadline at millisecond tails. Every snapshot-based
managed policy missed deadlines heavily, and envelope awareness made things
worse than plain idle-LRU, not better. The governance thesis (authority
enforced, integrity preserved) is untouched: 0 authority violations and 0
integrity failures in every run.

## Setup (`runner.py`, `host.py`, `traces.py`)

- **Load:** 300 tenants, each 4 MiB logical at 25% density (about 1 MiB of
  touched state). All share one engine and module (VRE-1E packing). The
  trace runs 90 s of real time at about 250 requests/s.
- **Budget:** 160 MiB, a cgroup v1 memory limit, the same for every policy.
  - **native** may swap (a 2 GiB swap file is on).
  - The **managed** policies may not (memsw = budget), so any overrun would
    be an OOM kill. There were none.
- **Trace:** the model in `traces.py` was fixed before any measurement.
  - Tenants have Zipf popularity (s = 1.1).
  - Each is interactive (100 ms deadline) or batch (1000 ms).
  - Authority windows are 10–30 s long, one to three per tenant.
  - 5% of requests fall outside authority and must be denied.
  - The **control** trace ("uninformative") makes every tenant interactive
    with one window covering the whole run, so the envelope says nothing
    recency does not.
- **Policies:**
  - **native:** all tenants live; the kernel pages.
  - **idle-lru:** demote after 5 s idle, evict least recently used.
    Demotion writes a snapshot and fsyncs it; restore is inline.
  - **envelope:** idle-lru, plus demote when the authority window ends and
    evict outside-authority, then batch, then interactive.
  - **predictive:** envelope, plus speculative non-evicting
    materialization when a window opens.
- **Live cap:** managed policies get 85% of the budget, minus their own
  baseline, divided by the 1.2 MB per-tenant cost measured in VRE-1E.

## Results: run `20261011T061749Z` (with the fix described below)

| Trace | Policy | Interactive ≤100 ms | Interactive p50 / p95 / p99 (ms) | Batch ≤1 s | Mean resident | Restores | Snapshot writes | Swap during run (pages in / out) |
|---|---|---|---|---|---|---|---|---|
| informative | native | **100%** | **0.30 / 0.62 / 1.85** | **100%** | 159 MiB | 0 | 0 | 2,576 / 5,251 |
| informative | idle-lru | 68.2% | 35.7 / 529 / 709 | 100% | 114 MiB | 1,562 | 1.34 GB | 0 / 0 |
| informative | envelope | 60.9% | 46.2 / 1,463 / 1,588 | 93.4% | 111 MiB | 1,573 | 1.35 GB | 0 / 0 |
| informative | predictive | 58.4% | 54.9 / 1,191 / 1,410 | 94.8% | 112 MiB | 1,644 | 1.43 GB | 0 / 0 |
| control | native | **100%** | **0.33 / 0.63 / 1.49** | — | 160 MiB | 0 | 0 | 2,553 / 5,078 |
| control | idle-lru | 1.4% | 36,339 / 71,619 / 75,128 | — | 127 MiB | 4,766 | 4.77 GB | 0 / 0 |
| control | envelope | 1.3% | 36,149 / 70,558 / 73,865 | — | 127 MiB | 4,766 | 4.77 GB | 0 / 0 |
| control | predictive | 0.06% | 37,315 / 74,892 / 78,182 | — | 127 MiB | 4,819 | 4.82 GB | 0 / 0 |

- Native's warm-up (bringing all 300 tenants live under the limit) swapped
  out about 43,300 pages (169 MB) before the trace started. During the run
  it took about 1,700 major faults.
- Every run: 0 authority violations, 0 integrity failures, 0 OOM kills.
  The 730 out-of-authority requests in the informative trace were denied in
  every run.

## What it means

1. **Page granularity wins.** A request here touches one or two 4 KiB
   pages of a tenant's hot set. The kernel brings back only those pages.
   The managed policies bring back the whole tenant (about 1 MiB, inline)
   and write a whole snapshot, fsynced, on every eviction. That is 1.3 to
   4.8 GB of writes per 90 s. Against that mechanism, no eviction order
   could have closed a gap of this size.
2. **Envelope awareness made things worse, not better.** On the informative
   trace, envelope met 60.9% and predictive 58.4% of interactive deadlines,
   against 68.2% for idle-lru. Batch deadlines also fell, to 93–95% from
   100%. The likely reason, inferred from the trace rather than isolated in
   a separate run, is that demoting at window end discards residency a
   recurring tenant needs again at its next window. Batch-first eviction
   then pushes the restore cost onto batch tenants.
3. **The control behaves as a control should.** Where the envelope carries
   no information, the three managed policies are indistinguishable
   (1.3–1.4%, 4,766 restores each). So the differences on the informative
   trace come from the envelope signals, not from noise. Those signals hurt
   here.
4. **Lower memory is not the win it looks like.** The managed policies kept
   mean resident memory at 111–127 MiB against native's 159 MiB, but the
   budget was 160 MiB for all of them. Native used the allowance it was
   given and met every deadline. The managed policies left memory unused
   and missed deadlines. "Useful work per GiB" scores the managed policies
   higher only because its denominator is smaller. It is not a fair
   comparison at the same budget, so no claim is made from it.

## The first run (`20261011T053146Z`), kept as found

The first full run used an envelope policy that judged authority expiry at
wall-clock time. Once the host fell behind real time, it demoted tenants
right after restoring them to serve backlogged requests that had been
authorized on arrival. That produced about 14,700 restore/demote cycles,
against 1,562 for idle-lru, and 17–19% of deadlines met.

That was a defect in the policy, not a property of envelope awareness. The
fix judges idle, expiry and prefetch at the trace time of the oldest waiting
request, and is in `host.py`. With the fix, envelope restores match
idle-lru (1,573 against 1,562), as the table above shows, and the
conclusion still holds.

A small trial before that showed a prefetch storm in the first predictive
design. Prefetch was made non-evicting and limited to one per housekeeping
pass before any full run.

## Not tested, and the next fair experiment

- **This is one host, one workload shape (a small hot set per tenant), one
  budget and one trace model.** A workload that touches most of its state
  on every request, or with dormancy long enough to empty swap, could rank
  the policies differently. Neither was measured.
- **Snapshot demotion has real uses swap cannot cover:**
  - moving state to another host (VRE-2);
  - surviving host loss;
  - dropping state outright when authority ends for good.

  None of these is a latency win, and none was measured here.
- **The next fair test:** keep the kernel's page-granular mechanism and let
  the envelope steer it. On authority expiry, call `madvise(MADV_PAGEOUT)`
  on the tenant's linear memory; when a window opens, call
  `madvise(MADV_WILLNEED)`. Compare that against plain native at the same
  budget, including a budget tight enough that native starts to miss
  deadlines. That tests the brief's own position: *make OS mechanisms
  subordinate to the contract*, instead of replacing them.
