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

A follow-up (below) let the envelope steer the kernel's own paging with
`madvise` instead of replacing it. Across budgets from 160 down to 48 MiB it
did not improve deadlines either, cost 37–50× the swap-in I/O, and hurt the
control trace. At tight budgets (16–8 MiB), where native itself degrades, the
one consistent signal (16 MiB) failed a pre-registered confirmation on fresh
seeds. See **VRE-1.5 verdict** at the end.

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
- **The next fair test,** run below: keep the kernel's page-granular
  mechanism and let the envelope steer it, which is the brief's own position
  (*make OS mechanisms subordinate to the contract*).

## Envelope-steered kernel paging: run `20261011T064045Z`

**Policy `native-steered`:** native (every tenant live, kernel paging, swap
allowed), plus:
- `madvise(MADV_PAGEOUT)` over a tenant's linear memory when its authority
  window closes;
- `madvise(MADV_WILLNEED)` when a window opens.

Everything else (trace, seed, tenants, cgroup limit, swap) is identical to
native. The sweep runs both policies at budgets of 160, 96, 64 and 48 MiB.

**Resident figure:** the table uses the cgroup's own charge
(`memory.usage_in_bytes`), not process RSS. Under PAGEOUT, RSS falls (to
20–46 MiB) while the cgroup charge stays at the limit: the pages sit in swap
cache and are still charged. A trial showed that the RSS "saving" was
therefore not a saving, so RSS is not used here.

| Budget | Trace | Policy | Interactive ≤100 ms | Interactive p50 / p95 / p99 (ms) | Mean / peak cgroup charge (MiB) | Swap pages in / out during run | Major faults | madvise open / close |
|---|---|---|---|---|---|---|---|---|
| 160 | informative | native | 100% | 0.34 / 0.69 / 3.07 | 159.9 / 160.0 | 2,624 / 4,980 | 1,656 | — |
| 160 | informative | native-steered | 100% | 0.29 / 0.69 / 2.37 | 159.9 / 160.0 | 97,999 / 40,611 | 1,552 | 605 / 604 |
| 160 | control | native | 99.99% | 0.31 / 0.63 / 1.99 | 159.9 / 160.0 | 2,662 / 5,122 | 1,598 | — |
| 160 | control | native-steered | 99.32% | 0.32 / 0.68 / 19.16 | 159.9 / 160.0 | 78,922 / 36,465 | 1,853 | 300 / 0 |
| 96 | informative | native | 100% | 0.34 / 0.72 / 1.90 | 95.9 / 96.0 | 2,860 / 5,525 | 1,842 | — |
| 96 | informative | native-steered | 100% | 0.33 / 0.95 / 3.44 | 95.9 / 96.0 | 125,831 / 26,038 | 2,176 | 605 / 604 |
| 96 | control | native | 100% | 0.37 / 0.74 / 3.17 | 95.9 / 96.0 | 2,832 / 8,568 | 1,787 | — |
| 96 | control | native-steered | 99.27% | 0.37 / 0.80 / 11.35 | 95.9 / 96.0 | 79,236 / 20,524 | 2,099 | 300 / 0 |
| 64 | informative | native | 100% | 0.32 / 0.77 / 2.78 | 63.9 / 64.0 | 2,831 / 5,429 | 1,990 | — |
| 64 | informative | native-steered | 100% | 0.34 / 0.96 / 2.40 | 63.9 / 64.0 | 139,966 / 19,250 | 3,173 | 605 / 604 |
| 64 | control | native | 99.98% | 0.36 / 0.74 / 2.63 | 63.9 / 64.0 | 2,863 / 6,830 | 1,841 | — |
| 64 | control | native-steered | 99.16% | 0.35 / 0.77 / 42.20 | 63.9 / 64.0 | 79,336 / 12,590 | 2,263 | 300 / 0 |
| 48 | informative | native | 100% | 0.32 / 0.69 / 1.89 | 47.9 / 48.0 | 2,952 / 5,956 | 1,891 | — |
| 48 | informative | native-steered | 100% | 0.33 / 0.99 / 2.52 | 47.9 / 48.0 | 147,629 / 16,489 | 4,274 | 605 / 604 |
| 48 | control | native | 100% | 0.37 / 0.77 / 2.71 | 47.9 / 48.0 | 2,781 / 5,486 | 2,098 | — |
| 48 | control | native-steered | 99.33% | 0.32 / 0.69 / 10.19 | 47.9 / 48.0 | 79,335 / 8,731 | 2,297 | 300 / 0 |

Notes on the runs:
- Batch deadlines were met 100% in every informative run.
- Every run: 0 OOM kills, 0 integrity failures, 0 authority violations, 0
  madvise errors.
- Warm-up swap-out before the trace grows as the budget shrinks (about
  43,000 pages at 160 MiB, 71,000 at 48 MiB), the same for both policies.

### What it shows

1. **Steering did not improve deadlines at any budget.** On the informative
   trace, both policies met 100% of interactive and batch deadlines at every
   budget. The p99 moved in both directions (lower at 160 and 64 MiB,
   higher at 96 and 48 MiB) by about a millisecond. With one run per cell,
   a difference that small and inconsistent cannot be told apart from noise,
   so no claim is made from it.
2. **Steering costs a lot of I/O for nothing measurable.** Steered runs
   swapped in 37 to 50 times as many pages (98,000–148,000 against
   2,600–3,000). PAGEOUT at window close evicts pages the kernel would
   have kept, and WILLNEED at the next open reads them back. The kernel's
   own reclaim, which sees actual access, needed far less.
3. **On the control, steering hurts.** There, every window opens at t = 0,
   so WILLNEED asks for all 300 tenants at once, about 79,000 pages, under a
   limit that cannot hold them. The misses sit at the start of the run:
   - at 160 MiB, all 152 misses arrive in the first 0.6 s;
   - at 48 MiB, 137 of 148 arrive in the first 5 s.

   Native met 99.98–100% of control deadlines; steered met 99.16–99.33%.
4. **This sweep never reached the budget where native fails.** Native met
   at least 99.98% of interactive deadlines even at 48 MiB, against a
   logical population of 1.2 GiB and about 300 MiB of touched state.
   Requests touch one or two pages of a Zipf-popular tenant, so the hot
   working set is small and the kernel keeps it resident. So the case where
   steering was meant to help (native degrading under a tight budget) did
   not arise at these budgets.

**Verdict, extended:** on this host and workload, envelope awareness has not
improved a defined metric over native OS behaviour at the same budget,
whether it replaces the kernel's mechanism (snapshot policies) or steers it
(madvise). The governance results are unchanged: authority enforced,
integrity preserved, in every run.

## Tight budgets, single runs: run `20261011T071126Z`

**Purpose:** find the budget where native starts to miss deadlines, and test
a third policy, **native-pageout**. It is native-steered without the
window-open WILLNEED: it only calls PAGEOUT when a window closes.

**Setup:** budgets of 24, 16, 12 and 8 MiB; three policies; both traces;
one run per cell. Every run had 0 OOM kills, 0 integrity failures, 0
authority violations and 0 madvise errors. Batch deadlines were met 100% in
every informative run.

| Budget | Trace | native ≤100 ms (p99 ms) | native-steered ≤100 ms (p99 ms) | native-pageout ≤100 ms (p99 ms) | Swap-ins: native / steered / pageout |
|---|---|---|---|---|---|
| 24 | informative | 99.97% (4.3) | 100% (5.5) | 99.95% (3.2) | 3,597 / 160,845 / 3,414 |
| 24 | control | 100% (3.4) | 99.55% (7.1) | 99.98% (2.6) | 4,396 / 81,198 / 4,166 |
| 16 | informative | 97.90% (218.9) | 99.57% (57.9) | 99.83% (6.9) | 7,376 / 167,895 / 6,255 |
| 16 | control | 99.62% (25.7) | 99.39% (10.3) | 99.79% (5.5) | 9,377 / 86,159 / 9,210 |
| 12 | informative | 99.04% (89.7) | 99.68% (11.9) | 99.30% (35.5) | 11,398 / 183,068 / 11,560 |
| 12 | control | 99.77% (6.4) | 99.28% (64.0) | 99.55% (5.5) | 16,799 / 93,431 / 16,935 |
| 8 | informative | 98.59% (180.1) | 98.58% (288.9) | 98.71% (228.1) | 43,387 / 245,855 / 55,664 |
| 8 | control | 96.60% (452.2) | 97.92% (433.6) | 99.46% (10.8) | 84,480 / 143,169 / 65,474 |

### Single runs here are noise-dominated, so no claim is made from this table

On the control trace, no authority window closes during the run. So
native-pageout issues no advice at all: the manifest records
`{"open": 0, "close": 0}`. It is mechanically identical to native. Its
difference from native in the control rows is therefore pure run-to-run
variation:

| Budget | native | native-pageout (same mechanism) | Gap in deadlines met | p99 (ms) |
|---|---|---|---|---|
| 24 MiB | 100% | 99.98% | 0.02 points | 3.4 vs 2.6 |
| 16 MiB | 99.62% | 99.79% | 0.17 points | 25.7 vs 5.5 |
| 12 MiB | 99.77% | 99.55% | 0.22 points | 6.4 vs 5.5 |
| 8 MiB | 96.60% | 99.46% | 2.9 points | 452 vs 10.8 |

The tail is dominated by occasional stall episodes under reclaim, so
identical configurations can differ by 40× at p99. Native is also
non-monotonic in the budget: 97.9% at 16 MiB but 99.04% at 12 MiB.

So the informative-trace gaps cannot be read as effects from single runs,
even though some are large (native 97.9% against native-pageout 99.83% at
16 MiB). The sign of the gap between the mechanically identical policies
also flips from budget to budget, so running first in a cell is not a
consistent bias. The next run repeats the informative cells at 16, 12 and 8
MiB over five seeds, with paired comparisons per seed and the policy order
rotated from cell to cell.

What does hold at every budget: native-steered's WILLNEED drives 1.7 to 45
times native's swap-in I/O. native-pageout's swap-ins stay within about
±30% of native's.

## Paired repeats over five seeds: run `20261011T075620Z`

**Setup:** budgets of 16, 12 and 8 MiB, the informative trace, seeds 1–5,
and native, native-steered and native-pageout. Within each (budget, seed)
cell all three policies replay the same trace. The policy order rotates
from cell to cell (`--rotate-order`), and each run's position is recorded.

**Analysis:** `python -m vre15.paired vre15/runs/20261011T075620Z/manifest.json`
gives per-seed paired differences against native and an exact two-sided
sign test. With five seeds even 5 of 5 wins gives p = 0.0625, so this run
alone cannot reach p < 0.05.

**Clean runs:** 44 of 45 runs completed, with 0 integrity failures and 0
authority violations. Batch deadlines were met 100% in every run at 16 MiB,
in all but one at 12 MiB (99.37%), and in all but two at 8 MiB (98.56% and
99.82%).

**One kill:** native at 16 MiB, seed 1, was OOM-killed during warm-up,
before serving anything (exit -9; the cgroup recorded `oom_kill 1`).
- Swap was on, with 1.7 GB free, swappiness 60 and memsw unlimited.
- The kernel log shows a memcg OOM on a `GFP_HIGHUSER_MOVABLE` fault with
  11 MiB anon resident: reclaim could not keep pace with warm-up allocation
  under the limit.
- Warm-up is identical for all three policies (every tenant materialized,
  no advice yet), so the kill reflects the kernel under a 16 MiB limit,
  not the native policy.
- The kill is kept in the manifest, and `paired.py` leaves that seed out of
  the native pairs rather than scoring it.

| Budget | Policy vs native | Wins / losses | Sign test p | Median Δ deadlines met | Median Δ p99 | Interactive ≤100 ms, mean (min) | Median swap-ins |
|---|---|---|---|---|---|---|---|
| 16 | native | — | — | — | — | 99.28% (97.84%), 4 seeds | 7,584 |
| 16 | native-pageout | 4 / 0 | 0.125 | +0.19 pts | −1.0 ms | 99.89% (99.74%) | 6,115 |
| 16 | native-steered | 4 / 0 | 0.125 | +0.29 pts | +0.2 ms | 99.95% (99.82%) | 165,893 |
| 12 | native | — | — | — | — | 98.55% (96.20%) | 11,177 |
| 12 | native-pageout | 2 / 3 | 1.0 | −0.14 pts | −2.2 ms | 99.05% (97.15%) | 12,211 |
| 12 | native-steered | 2 / 3 | 1.0 | −0.20 pts | +4.7 ms | 99.28% (99.12%) | 183,197 |
| 8 | native | — | — | — | — | 97.54% (95.05%) | 45,949 |
| 8 | native-pageout | 1 / 4 | 0.375 | −1.31 pts | +33.9 ms | 95.20% (91.23%) | 56,125 |
| 8 | native-steered | 3 / 2 | 1.0 | +0.06 pts | +21.4 ms | 98.15% (97.57%) | 259,455 |

### What it shows

1. **16 MiB is the only budget with a consistent direction.** Both steered
   policies met more interactive deadlines than native on all 4 seeds where
   native completed.
   - The median gain is small: 0.19 and 0.29 points.
   - Most of the mean gain comes from native's occasional collapse
     episodes, which the steered policies did not show. Seed 5 is the
     clearest: native met 97.84% at a p99 of 283 ms.
   - p = 0.125 for each, which is not significant.
2. **The direction does not survive tighter budgets.**
   - At 12 MiB both policies split 2–3 against native.
   - At 8 MiB native-pageout is worse than native on 4 of 5 seeds (median
     −1.31 points), and native-steered splits 3–2.
   - An effect that appears at one budget, vanishes at the next and
     reverses at the one after is not yet a property of envelope awareness.
3. **native-steered lowers the variance, not the typical case.** Its worst
   seed is much better than native's at 12 MiB (99.12% against 96.20%) and
   at 8 MiB (97.57% against 95.05%). Its median p99 is worse at both, and
   it pays 5.6× (8 MiB) to 22× (16 MiB) native's swap-in I/O.
4. **No order effect large enough to matter.** Mean interactive
   within-deadline by position in cell was 98.24%, 98.54% and 98.78% for
   first, second and third, against a spread of several points between
   seeds. Rotation balances each policy across positions.

**Verdict, unchanged:** no budget shows a statistically supported
improvement of envelope-aware residency over native at the same budget. The
one consistent signal (16 MiB) is small, rests on 4 seeds, and depends on
native's tail collapses. It is a hypothesis, tested next on fresh seeds.

## Pre-registered confirmation (fixed before the run)

Written and committed before any run with these seeds.

- **Hypotheses:**
  - **H1:** at 16 MiB on the informative trace, native-pageout meets more
    interactive deadlines than native.
  - **H2:** the same for native-steered against native.
- **Seeds:** fresh, 101–110 (10 seeds). Nothing else changes: 300 tenants,
  90 s, 250 req/s, the same three policies, `--rotate-order`.
- **Test:** for each hypothesis, an exact two-sided sign test on the paired
  per-seed within-deadline shares from `vre15/paired.py`.
  - Seeds where either run failed are excluded and reported.
  - Ties are excluded.
  - The threshold is α = 0.025 per hypothesis (Bonferroni over the two).
- **Decision:** a hypothesis is confirmed only if the policy wins more
  seeds than it loses and p < 0.025. With all 10 seeds usable, that means
  at least 9 wins out of 10 (p = 0.0215). Anything else is reported as
  not confirmed, with the numbers.
- **Not a criterion:** p99, swap I/O and memory are reported but do not
  decide the outcome.

## Confirmation result: run `20261011T092300Z`

**Neither hypothesis is confirmed.** The run followed the pre-registration
exactly: 16 MiB, informative trace, seeds 101–110, three policies, rotated
order. All 30 runs completed, with 0 OOM kills, 0 integrity failures and 0
authority violations. Batch deadlines were met 100% in every run except
native on seed 104 (98.8%).

Recompute with:
`python -m vre15.paired vre15/runs/20261011T092300Z/manifest.json`

| Hypothesis | Wins / losses / ties | Sign test p | Required | Outcome | Median Δ deadlines met | Median Δ p99 |
|---|---|---|---|---|---|---|
| H1: native-pageout > native | 8 / 2 / 0 | 0.109 | ≥ 9 wins, p < 0.025 | **not confirmed** | +0.16 pts | −1.2 ms |
| H2: native-steered > native | 6 / 3 / 1 | 0.508 | p < 0.025 | **not confirmed** | +0.16 pts | +1.0 ms |

| Policy | Interactive ≤100 ms, mean (min) over 10 seeds | p99 median (max), ms | Median swap-ins |
|---|---|---|---|
| native | 99.59% (97.95%) | 4.3 (780.5) | 6,764 |
| native-pageout | 99.76% (98.79%) | 3.2 (138.4) | 6,595 |
| native-steered | 99.87% (99.37%) | 5.0 (34.8) | 167,343 |

**What it shows:**
- Both steered policies lean the same way as in the five-seed repeat, but
  less consistently. Each lost to native on some fresh seeds:
  - native-pageout on seeds 103, 104 and 107;
  - native-steered on 105, 106 and 109.
- The gain in mean is about 0.2–0.3 points. It comes mostly from removing
  native's rare collapse episodes, as on seed 104, where native met 97.95%
  at a p99 of 780 ms.
- native-steered again buys a better worst case (min 99.37%, max p99 35 ms)
  with about 25× native's swap-in I/O.
- The mean within-deadline share by position in cell was 99.65%, 99.91% and
  99.67% (first, second, third). That shows no consistent order effect.

## VRE-1.5 verdict

The brief's falsifier asks whether VRE-aware residency control can
**materially** improve a defined metric over native OS behaviour at the
same physical budget. On this host and workload, across every experiment
here, it has not:

- **Replacing the kernel's mechanism** (snapshot demotion: idle-LRU,
  envelope, predictive) is far worse than native at every budget tested.
  Envelope awareness made it worse still.
- **Steering the kernel's mechanism** (`madvise` PAGEOUT and WILLNEED)
  shows no improvement where native already meets its deadlines (160–24
  MiB). It costs up to 50× the swap-in I/O, and hurts the control trace.
- **At the tight budgets where native degrades** (16, 12, 8 MiB):
  - The only consistent direction was at 16 MiB.
  - It did not pass the pre-registered confirmation (H1 p = 0.109, H2
    p = 0.508 against α = 0.025).
  - It does not hold at 12 MiB, and reverses for native-pageout at 8 MiB.

**The performance thesis therefore fails this falsifier as measured.** What
remains is a narrow, unconfirmed hypothesis: that envelope-timed PAGEOUT
trims native's rare reclaim collapses near one specific budget. That would
need a larger, separately pre-registered study, on more than one host,
before anyone relies on it.

**The governance thesis is untouched.** The six committed runs hold 131
policy runs. All 130 that completed enforced authority exactly (0
violations) and preserved tenant state exactly (0 integrity failures); the
other was the one OOM-killed in warm-up before serving any request. That holds for snapshot
demotion, kernel paging and steering alike.

## Second attempt: keep request threads out of reclaim

After the confirmation failed, a research pass surveyed what could still
beat native at the same budget. The report is kept outside the repository
as "Envelope aware paging mechanisms". Its main point, reasoned from the
kernel source:
- On cgroup v1 with the classic LRU (this host has no MGLRU), the task
  that hits the memcg limit reclaims synchronously.
- That task can wait, with no timeout, for swap writeback it queued itself
  (`folio_wait_writeback` in `shrink_folio_list`).
- That fits native's 200–900 ms collapses.
- The published p99 wins over stock swap (Hermit, Canvas, REAP, FaaSnap)
  come from taking that work off the fault path, not from better
  prediction.

**Policies** (`host.py`, `Reclaimer`): native kernel paging, plus a
background thread that keeps a free band below the limit.
- The band is the larger of 2 MiB and 1/8 of the budget.
- The thread pages out whole tenants with `MADV_PAGEOUT`, at most 4 per
  pass.
- It never touches a tenant requested in the last 2 s.

| Policy | Victim order |
|---|---|
| **envelope-headroom** | Outside authority first, furthest next window opening first (Belady with known windows), then LRU |
| **lru-headroom** | Recency only. The control: background reclaim without the envelope |

Development smoke runs (20 s, 16 MiB) used seed 104 only. They also caught
a fall-through bug: the new policies ran the snapshot policies' idle
demotion and thrashed. It is fixed, and the committed-runs test now fails
any kernel-paged run that demotes or restores.

## Pre-registered test of the headroom policies (fixed before the run)

Written and committed before any run with these seeds.

- **Hypotheses:**
  - **H3:** at 16 MiB on the informative trace, envelope-headroom meets
    more interactive deadlines than native.
  - **H4:** at 16 MiB on the informative trace, envelope-headroom meets
    more interactive deadlines than lru-headroom. This isolates what the
    envelope adds over background reclaim alone.
- **Seeds:** fresh, 201–210 (10 seeds). Nothing else changes: 300 tenants,
  90 s, 250 req/s, the policies native, envelope-headroom and
  lru-headroom, `--rotate-order`, and the default band.
- **Test:** for each hypothesis, an exact two-sided sign test on paired
  per-seed within-deadline shares from `vre15/paired.py` (with
  `--baseline lru-headroom` for H4).
  - Failed runs are excluded and reported.
  - Ties are excluded.
  - The threshold is α = 0.025 per hypothesis (Bonferroni over the two).
- **Decision:** confirmed only if the policy wins more seeds than it loses
  and p < 0.025. With all 10 seeds usable, that means at least 9 wins.
  Anything else is reported as not confirmed, with the numbers.
- **Reported, not deciding:** lru-headroom against native, p99, swap I/O,
  cgroup memory and reclaimer statistics.

## Headroom result: run `20261011T125629Z`

**Neither hypothesis is confirmed.** The run followed the pre-registration
exactly.
- **Clean:** all 30 runs completed, with 0 OOM kills, 0 integrity
  failures, 0 authority violations, and 0 demotions or restores in any
  run.
- **Batch:** deadlines were met 100% except envelope-headroom on seed 205
  (98.6%).

| Hypothesis | Wins / losses / ties | Sign test p | Outcome | Median Δ deadlines met | Median Δ p99 |
|---|---|---|---|---|---|
| H3: envelope-headroom > native | 4 / 6 / 0 | 0.754 | **not confirmed** | −0.22 pts | +2.8 ms |
| H4: envelope-headroom > lru-headroom | 3 / 7 / 0 | 0.344 | **not confirmed** | −0.05 pts | +2.4 ms |
| (reported) lru-headroom vs native | 3 / 7 / 0 | 0.344 | — | −0.29 pts | +17.2 ms |

| Policy | Interactive ≤100 ms, mean (min) | p99 median (max), ms | Median swap-ins | Mean cgroup charge |
|---|---|---|---|---|
| native | 99.73% (99.29%) | 5.6 (58.1) | 6,483 | 15.9 MiB |
| envelope-headroom | 99.22% (96.44%) | 6.0 (1,293) | 7,107 | 15.9 MiB |
| lru-headroom | 99.44% (98.84%) | 23.6 (165) | 6,907 | 15.9 MiB |

**What it shows:**
- **The precondition was never reached.** The cgroup charge sat at the
  limit (15.9 MiB) in every headroom run. The reclaimer issued about 1,850
  tenant page-outs per run, yet never opened a free band.
- Likely reasons, not isolated:
  - the charge includes page cache, which refills freed space and which
    tenant page-outs cannot reclaim;
  - `MADV_PAGEOUT` queues swap writes and returns, and a request thread
    that later reclaims can meet those pages still under writeback and
    wait on them. That would be the very stall the policy was meant to
    remove.
- Background page-out made both policies worse than native, with longer
  tails and a 1.3 s p99 on one seed.

## Oracle bound: how much does the envelope know beyond recency?

`vre15/oracle.py` replays the same seeded traces through a page-granular
cache model (16 hot pages per tenant, one page per request) and counts
demand misses under four policies:
- LRU, the stand-in for the kernel;
- envelope eviction, which uses window times only;
- envelope eviction plus hot-set prefetch at window open;
- Belady OPT, which knows every future request.

No timing, kernel or root is involved. Seeds 201–210 are on the informative
trace and 201–203 on the control. The raw rows are in
`vre15/oracle_runs/20261011-seeds201-210.json`.

| Capacity (share of 4,800 hot pages) | LRU misses | Envelope | Envelope + prefetch (prefetch reads) | OPT | Gap closed by envelope, mean [min, max] | Demand-miss cut, env + prefetch vs LRU |
|---|---|---|---|---|---|---|
| 5% | 8,547 | 8,398 | 8,358 (+365) | 5,135 | 0.04 [0.04, 0.05] | 2.2% |
| 10% | 6,150 | 5,860 | 5,733 (+880) | 3,926 | 0.13 [0.12, 0.15] | 6.8% |
| 25% | 4,132 | 3,702 | 2,681 (+3,013) | 3,394 | 0.58 [0.53, 0.66] | 35.1% |
| 50% | 3,538 | 3,394 | 85 (+6,047) | 3,394 | 1.00 | 97.6% |
| control, 25% | 5,957 | 5,957 | 5,792 | 3,835 | 0.00 | 2.8% |

**Claim classes:**
- **Observed (model):**
  - At tight capacity (≤10% of the hot set), envelope eviction closes only
    4–15% of the LRU-to-OPT gap. There, the envelope's eviction knowledge
    is nearly redundant with recency.
  - At 25% it closes 53–66%.
  - The control closes 0.00 at every capacity, as it must.
- **Observed (model):** at moderate capacity most remaining misses are
  first touches after a window opens.
  - Recency cannot anticipate them; the envelope can.
  - Prefetching the hot set at window open removes 35% of demand misses at
    25% capacity and 98% at 50%.
  - The cost is extra reads: +38% and +73% total I/O respectively.
- **Inferred:** native-steered failed on mechanism, not information.
  - Its window-open WILLNEED covered each tenant's whole 4 MiB region,
    about 256 resident pages.
  - The model's prefetch needs only the 16-page hot set: about 3,000 reads
    per run, against the 98,000–160,000 swap-ins measured.
  - That over-fetch is 30–50×.
- **Hypothesis (next test):** a working-set-targeted prefetch captures
  the envelope's real advantage without the I/O storm.
  - At window close, record which of the tenant's pages are resident with
    `mincore`; that is its recent working set.
  - Ahead of the next window opening, fault exactly those pages back in on
  a background thread with `MADV_POPULATE_READ`, paced.
  - It uses only what the runtime can observe. It does not read the guest
    code.
- **Unverified:** where 16 MiB sits on this capacity axis. The process's
  non-tenant memory shares the budget, and swap-in counts include
  readahead, so the mapping is not calibrated.

### What a runtime can actually prefetch

The prefetch row above fetches each tenant's full hot set at every window
opening, including its first. A runtime cannot know a first window's pages
without reading the guest. `oracle.py` therefore adds
**envelope+ws-prefetch**: at an opening, fetch only the pages the tenant
touched in its previous window, and nothing for a first window. This is
what the `envelope-prefetch` policy in `host.py` does, recording the
working set with `mincore` at window close.

| Capacity | Envelope eviction | + previous-window prefetch (reads) | + full hot-set prefetch |
|---|---|---|---|
| 5% | −1.7% | −1.6% (+55) | −2.2% |
| 10% | −4.7% | −4.4% (+162) | −6.8% |
| 25% | −10.4% | −14.9% (+543) | −35.1% |
| 50% | −4.1% | −4.1% (+0) | −97.6% |

The figures are demand misses against LRU, as means over seeds 201–210.
The raw rows are in `vre15/oracle_runs/20261011-seeds201-210-ws.json`.

**Claim classes:**
- **Observed (model):** what a runtime can learn from history gains little
  over envelope eviction alone. The full hot-set prefetch gains are first
  touches in a tenant's first window. History cannot predict those, and
  the envelope as specified does not carry them. At 50% capacity the
  previous window's pages are still cached, so there is nothing left to
  fetch.
- **Observed (smoke run, development seed 104, 40 s):**
  - `envelope-prefetch` works mechanically: 0 errors, 0 late prefetches,
    about 0.5 ms per tenant.
  - It fired on only 15 of about 600 openings, at about 4 pages each. Most
    openings were first windows, and at 16 MiB the kernel had already
    evicted most of a tenant's hot set before its window closed.
- **Decision:** `envelope-prefetch` is **not given a pre-registered run**.
  Its oracle bound is at most a 15% cut in demand misses at one capacity.
  Native's deadline misses come from rare reclaim stalls, not from fault
  counts: about 4,000 faults per run still meet 99.7% of deadlines. A
  15% fault cut is not expected to show up as a deadline gain at this
  sample size. Recorded here so it is not mistaken for an untested idea.
- **Aspirational thesis:** the envelope as a working-set declaration.
  - The large gains need to know what state an operation will touch
    before it first runs. CAPPO already grants one bounded operation per
    envelope.
  - If the grant also carried the tenant's recorded working set from its
    previous execution (as REAP stores a working-set file alongside a
    snapshot), first-window misses would become prefetchable. The model
    puts that at 35–98% fewer demand misses at moderate capacity.
  - Conditions:
    - the working set must be recorded by the runtime, for example with
      soft-dirty bits during execution, and stored with the governed state;
    - it must be signed or bound to the snapshot like the rest of the
      state;
    - stale working sets must cost no more than they save.
  - Not built, not measured.

## Release on revocation: explore wide, then confirm narrow

The only envelope action with a consistent direction so far is releasing a
tenant's memory when its authority ends. Two results point that way:
- native-pageout won 4/0 at 16 MiB in the five-seed repeat;
- it won 8/2 in the confirmation, at p = 0.109, which is not confirmed.

Acquiring early has made things worse every time: WILLNEED, headroom
reclaim, and the declared-working-set prefetch.

A single design failing tests that design only, so the next step tries
every reasonable variant of release on revocation. With many variants and
few seeds, one will look good by chance, so the two stages are kept apart.

**Stage 1, exploration (no claims):**
- The release variants:

  | Variant | Change from native-pageout |
  |---|---|
  | native-pageout | Baseline of the idea |
  | native-cold | `MADV_COLD`: deactivate without writing, so no swap writes stay in flight |
  | pageout-delay | Release 2 s after close; skip if authority resumes |
  | pageout-bg | The same release, on a background thread |
  | lru-pageout | The control: same timing, recency-chosen victim |

- A kernel factor, `vm.page-cluster` 0 against 3, applied to native too.
  If tuning readahead alone closes the gap, the envelope is not needed.
- **Budgets:** those inside the window found by the threshold sweep
  (`vre15.window`).
- **Seeds:** a small set, not reused anywhere else.

**Stage 2, confirmation:**
- **Pre-registration:** written and committed before the run.
- **What is tested:** the best variant from stage 1 against native, and
  against its matched recency control. The control decides whether the
  envelope's information matters, as opposed to doing eviction work at an
  idle moment.
- **Run:** fresh seeds, sign test, α = 0.025 per hypothesis.

## Threshold window: run `20261011T140814Z` (exploratory)

**Setup:** budgets of 10, 12, 14, 16, 20, 24 and 32 MiB; seeds 401–403 on
the informative trace; native, native-pageout, native-steered and
envelope-declared; `--record-ws` for every arm; rotated order. Read with
`python -m vre15.window`.

**Results:**
- 79 of 84 runs completed, with 0 integrity failures and 0 authority
  violations.
- 5 runs were OOM-killed in warm-up, before serving any request, all while
  copying state into anonymous memory:
  - native-steered at 12 MiB (seed 403) and 14 MiB (seed 401);
  - native-pageout at 14 MiB (seed 401);
  - envelope-declared at 14 and 16 MiB (seed 401).

| Budget | native, mean (min) | native p99 | native-pageout vs native | Its Δ request-thread major faults |
|---|---|---|---|---|
| 32 | 99.997% (99.99%) | 2.7 ms | 1W/0L, ±0 | +19 |
| 24 | 99.997% (99.99%) | 2.6 ms | 0W/0L, ±0 | −65 |
| 20 | 99.89% (99.76%) | 4.1 ms | 2W/0L, +0.05 pts | −142 |
| **16** | **98.59% (97.44%)** | **224 ms** | **3W/0L, +1.12 pts, p99 −222 ms** | **−10,977 (about −60%)** |
| 14 | 99.72% (99.44%) | 4.8 ms | 1W/1L of 2, +0.09 pts | −1,928 |
| 12 | 99.28% (99.05%) | 15.9 ms | 2W/1L, +0.56 pts | −6,806 |
| 10 | 98.92% (97.59%) | 6.6 ms | 2W/1L, +0.22 pts | +1,527 |

**What it shows (exploratory, three seeds; no claims):**
- **Onset:** native's mean falls below 99.9% from 20 MiB down.
- **Centre:** native collapses hardest at 16 MiB. Two of three seeds were
  below 99%, with p99 at 224 ms.
- **The candidate:** native-pageout leans positive at 20, 16, 12 and 10
  MiB.
  - At 16 MiB it won all three seeds.
  - It cut the request thread's own major faults by about 60%. That is a
    mechanism signal, separate from the deadline share.
- native-steered and envelope-declared also lean positive at 16 MiB.
  native-steered pays several thousand extra request-thread faults at
  every budget.
- **Metric note:** stall episodes per run, computed after the fact from
  the per-request results, are too coarse to separate policies. Most runs
  have about one, even at 32 MiB. A collapse is one severe episode, not
  many. Severity, measured by the within-deadline share and p99, is the
  informative measure.

16 MiB is the window's centre, and the next steps run there:
1. A/A for native, to test whether collapses are seed-driven or
   run-driven.
2. The sampler, to test whether collapses follow swap writeback.
3. A smoke test of the mapped policy.

## A/A at the window centre: run `20261011T163029Z`

**Setup:** native, 16 MiB, informative trace, seed 402 (native's worst
seed in the sweep), 10 identical repeats, with the sidecar sampler on.

| Repeat | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| Interactive ≤100 ms | **96.96%** | 99.92% | 99.73% | 99.92% | 99.55% | 99.77% | 99.64% | 99.91% | 99.43% | 99.88% |
| p99 (ms) | **688** | 4.0 | 9.1 | 2.9 | 22.0 | 4.8 | 12.8 | 3.4 | 42.7 | 5.0 |
| Missed interactive deadlines | 338 | 9 | 30 | 9 | 50 | 25 | 40 | 10 | 63 | 13 |

**Spread:** mean 99.47%, standard deviation 0.90 points. 1 of 10 runs
fell below 99%, and 2 of 10 below 99.5%.

**Severity is run-driven:** identical inputs gave anything from 9 to 338
missed deadlines.

**The trigger is trace-driven:**
- In 9 of 10 repeats the stall falls in the same second of the trace,
  t = 43.
- At t = 41.0–41.5 s the three most popular tenants all open their
  windows within half a second: t278 (interactive, 471 requests in 5 s),
  t199 and t28 (both batch).
- Their state starts cold, and requests jump from about 180/s to about
  380/s.

**The stall seconds coincide with reclaim and swap in both directions.**
Comparing the 14 stall seconds with the 896 calm ones:

| Signal | Stall seconds (mean / median) | Calm seconds (mean / median) |
|---|---|---|
| PSI memory "full" stall time, ms per second | 128.5 / 50.2 | 6.5 / 1.1 |
| Swap-outs per second | 1,377 / 1,948 | 350 / 61 |
| Swap-ins per second | 1,700 / 2,096 | 252 / 75 |
| Cgroup limit hits (failcnt) per second | 4,331 / 2,809 | 685 / 79 |

Instantaneous `nr_writeback` is similar in both: a 100 ms sample is too
coarse to see the queue.

**Claim classes:**
- **Observed:**
  - Collapse severity varies run to run on identical input, so seed
    pairing alone cannot separate policies.
  - The collapse is triggered by a known trace event: several hot
    tenants' windows opening at once.
  - It coincides with memory-pressure stalls, swap-outs, swap-ins and
    limit hits all rising together.
- **Inferred:** H-mech is supported but not isolated. During the burst
  the request thread reclaims under the limit while swap writes and reads
  are both heavy, and swap-in versus swap-out cannot be separated from
  these counters.
- **Design consequences:**
  - A test needs repeated runs per arm; comparing rates or means beats
    seed pairing.
  - The burst is an event the envelope knows in advance (signed window
    starts).
  - The mapped state model makes the reclaim it forces write-free for
    clean pages.

## State as a mapped snapshot: feasibility probe

So far every tenant's state has been copied into anonymous memory, and
anonymous pages can only be evicted by writing them to swap. That
assumption sits under both loss sources seen so far:
- the warm-up OOM kills, which come from copying all 300 tenants in at
  once;
- the collapses, if request threads wait on swap writes (H-mech, not yet
  tested).

The alternative treats the governed snapshot as the state and memory as a
cache of it. The tenant's linear memory is a copy-on-write mapping
(`MAP_PRIVATE | MAP_FIXED`) of a page-aligned image of its snapshot. Clean
pages can then be dropped with no write at all.

`vre15/mapped_probe.py` checked whether that works before any policy is
built on it. Report: `vre15/probes/20261011-mapped-snapshot.json` (kernel
6.18.44, wasmtime-py 44.0.0).

| Check | Result |
|---|---|
| A. Map a 4 MiB image over a live instance; guest digest equals the original's | **pass** |
| A. After 500 more steps on both, digests still equal | **pass** |
| B. Image file unchanged after guest writes (copy-on-write stays private) | **pass** |
| C. Mapped pages are charged to the faulting cgroup: a 64 MiB image (16 MiB of real state) read end to end peaks at 87 MiB, all as page cache | **pass**: same budget |
| D. That 64 MiB tenant read end to end inside a 16 MiB cgroup with swap forbidden (memsw = limit) | **pass**: digest correct, 0 OOM kills, 0 swap, 0.41 s |
| D. Control: the same state as an anonymous copy, same cgroup | **OOM-killed**, as expected |

**Claim classes:**
- **Observed:**
  - A wasmtime instance's linear memory can be a private file mapping of
    its snapshot, with exact state and private writes.
  - The mapping is charged to the tenant's cgroup.
  - Under a limit a quarter the size of its state, it runs with no swap at
    all, where the anonymous copy is killed.
- **Inferred:** an eviction under this state model writes nothing unless
  the page was modified since the image was taken. In this workload that
  is the 16-page hot set per tenant, against about 256 touched pages, so
  swap writes should shrink by roughly an order of magnitude. Warm-up
  becomes mapping, not copying.
- **Hypothesis (to test with H-mech):** if native's collapses are request
  threads waiting on swap writes, this state model removes most of the
  cause.
- **Honest framing:** if this wins, the win comes from the VRE's state
  model (governed snapshots that the runtime maps), not from envelope
  timing. A conventional application that maps its data files gets part
  of the same effect. The VEKLOM-specific part, still to build, is
  checkpoint on revocation: when authority ends, the modified pages are
  written into a new governed snapshot and the tenant is remapped clean,
  so the evidence trail and the memory release are one act.

### Mapped vs native smoke run: `20261011T164919Z`

**Setup:** 16 MiB, seed 402, 5 repeats per arm, rotated order,
`--record-ws`, sampler on. A smoke test: no claims.

| Arm | Completed | Warm-up OOM kills | Missed interactive deadlines | Warm-up swap-outs | Swap-outs during trace | Request-thread major faults |
|---|---|---|---|---|---|---|
| native | 3 / 5 | **2** | 38, 75, 100 | about 79,000 | 8,106–8,590 | 8,904–10,866 |
| mapped | 5 / 5 | **0** | 11, 35, 19, 17, 31 | 2,221–2,846 | 6,733–8,162 | 6,901–9,587 |

All completed runs had 0 integrity failures.

**Claim classes:**
- **Observed:**
  - Mapping instead of copying cuts warm-up swap writes about 30-fold.
  - mapped survived warm-up 5 of 5 times; native was killed 2 of 5 times.
  - Across today's runs, the anonymous-copy policies have had 9 warm-up
    OOM kills at 10–16 MiB in total.
- **Observed:** during the trace, swap-outs fall only about 12%.
  Trace-time writes are dominated by modified pages (hot sets, the
  recording phase's steps) and the process's anonymous heap, which
  mapping does not make clean.
  - mapped's misses look lower than these three native runs.
  - But native's 10-run A/A on the same seed and settings ranged from 9
    to 338 misses (median about 28), against mapped's median of 19.
  - A trace-time benefit is not established.
  - The stall still follows the same burst; in mapped it lands at trace
    second 46.
- **Hypothesis:** checkpoint on revocation would carry the benefit into
  the trace. It writes a revoked tenant's modified pages into a new image
  and remaps the tenant clean, which would make evicting the pages most
  worth evicting write-free. Not built.

## Pre-registered: mapped vs native (fixed before the run)

Written and committed before any run with these seeds.

- **Arms:** native and mapped. 16 MiB, informative trace, `--record-ws`,
  `--sample`, `--rotate-order`.
- **Seeds:** fresh, 601–615, one run per arm per seed (30 runs).
- **H11, admission robustness:** mapped has a lower warm-up OOM-kill rate
  than native.
  - The test is a two-sided Fisher exact test on killed versus completed
    runs.
- **H12, request-path latency:** mapped misses fewer interactive deadlines
  than native.
  - The test is an exact two-sided sign test on per-seed paired miss
    counts.
  - It uses seeds where both arms completed; ties are excluded.
- **Threshold:** α = 0.025 per hypothesis (Bonferroni over the two).
  Confirmed only if the direction favours mapped and p < 0.025.
- **Reported, not deciding:** within-deadline share, p99, swap I/O
  (warm-up and during the trace), request-thread major faults, and
  integrity.

### Result: run `20261011T170547Z`

**Neither hypothesis is confirmed.** The run followed the pre-registration
exactly. All 28 completed runs had 0 integrity failures and 0 authority
violations.

| Hypothesis | Result | Test | p | Outcome |
|---|---|---|---|---|
| H11: lower warm-up kill rate | native 2/15 killed, mapped 0/15 | Fisher exact, two-sided | 0.483 | **not confirmed** |
| H12: fewer missed interactive deadlines | mapped fewer in 7 of 13 pairs, more in 6, 0 ties | sign test | 1.000 | **not confirmed** |

Missed interactive deadlines per seed, as (seed, native, mapped):
(601, 5, 13), (602, 24, 1), (603, 24, 9), (604, 2, 4), (606, 45, 10),
(607, 3, 4), (608, 21, 66), (609, 24, 3), (610, 7, 15), (611, 28, 7),
(612, 5, 41), (614, 35, 21), (615, 16, 10).

| Arm | Completed | Interactive ≤100 ms, mean (min) | p99 median (max), ms | Warm-up swap-outs, median | Trace swap-outs, median | Request-thread major faults, median |
|---|---|---|---|---|---|---|
| native | 13 | 99.79% (99.49%) | 4.3 (11.6) | 79,019 | 8,107 | 8,328 |
| mapped | 15 | 99.84% (99.31%) | 3.9 (69.8) | 2,287 | 7,268 | 8,347 |

**Claim classes:**
- **Observed, holding in every run:** mapping cuts warm-up swap writes
  about 35-fold (median 79,019 to 2,287 pages).
- **Observed:** native's warm-up kill rate on these seeds was 2 of 15,
  much lower than the 2 of 5 in the smoke run. Fifteen runs per arm
  cannot separate 13% from 0%, so the admission-robustness benefit stays
  unconfirmed.
- **Observed:** during the trace, mapped and native are
  indistinguishable on missed deadlines (7–6), p99, swap-ins and request
  faults. Trace-time swap-outs are about 10% lower.
- **Inferred:** the mapped state model, as built, changes warm-up rather
  than steady state. During the trace the swap traffic is modified pages
  and heap, which mapping does not make clean. The remaining step that
  could change steady state is checkpoint on revocation (mapped-ckpt):
  it remaps revoked tenants clean.

## Checkpoint on revocation: smoke run `20261011T180001Z`

**Setup:** 16 MiB, seed 402, 3 repeats each of mapped-ckpt, mapped and
native, rotated order, `--record-ws`, `--sample`, `--probe-revoked`. A
smoke test: no claims. All 9 runs had 0 integrity failures, so checkpoint
and remap preserve state exactly.

An earlier attempt, `20261011T175453Z`, was aborted on a harness defect:
images were shared across runs. Its diagnosis is in that run's
`ABORTED.md`.

| Arm | Missed interactive deadlines | Where they fall (trace second: misses) | p99 (ms) | Revoked tenants with no resident pages at 0 / 1 / 5 s | Mean resident pages of a revoked tenant at 5 s | Swap-ins |
|---|---|---|---|---|---|---|
| native | 17, 30, 13 | all in second 36 | 2.8, 3.8, 3.2 | 4–5% / 5–6% / 11–14% | 5.7 | 9,401–10,063 |
| mapped | 19, 57, 11 | all in second 41 | 3.5, 10.0, 3.5 | 4–5% / 4–6% / 11–13% | 5.8–6.1 | 8,739–9,579 |
| **mapped-ckpt** | **10, 14, 2** | **scattered, 1–5 each; no burst second** | 28.8, 20.1, 6.1 | 4–6% / **54–56%** / **88–91%** | **1.4–2.0** | 10,583–12,300 |

There were 381 revocation probes per run.

**Checkpoints:**
- 415 per run, writing 5,433 pages in total; p50 3.1–3.4 ms, max 35–108
  ms (fdatasync on the request thread).
- Identical in every repeat: the same count, the same pages and the same
  first digest. Checkpoint evidence is deterministic and replayable.

**Claim classes (smoke, one seed):**
- **Observed:**
  - mapped-ckpt has no burst collapse. Native and mapped lose all their
    misses in one second, where the three hot tenants' windows open
    together; mapped-ckpt's misses are a handful, spread out.
  - Its total misses were lower in all 3 repeats.
- **Observed, governance:** with mapped-ckpt, 88–91% of revoked tenants
  have no state resident 5 s after authority ends, against 11–14% for
  native and mapped. Residency follows authority, measured.
- **Inferred:** by the time of the burst, tenants whose authority had
  ended were already checkpointed and released clean. The burst's faults
  find free memory instead of forcing reclaim with swap writes.
- **Costs:**
  - more swap-ins: pages are re-read from the image when a tenant returns;
  - a longer tail in 2 of 3 runs, because checkpoint fdatasync runs on the
    request thread.

## Pre-registered: checkpoint on revocation (fixed before the run)

Written and committed before any run with these seeds.

- **Arms:** native, mapped and mapped-ckpt. 16 MiB, informative trace,
  `--record-ws`, `--sample`, `--probe-revoked`, `--rotate-order`.
- **Seeds:** fresh, 701–715, one run per arm per seed (45 runs).
- **Hypotheses:** each is an exact two-sided sign test on per-seed paired
  values, over seeds where both arms completed, with ties excluded.

  | Hypothesis | Claim | Metric |
  |---|---|---|
  | H13 | mapped-ckpt misses fewer interactive deadlines than native | missed interactive deadlines |
  | H14 | mapped-ckpt misses fewer interactive deadlines than mapped (the checkpoint's own contribution) | missed interactive deadlines |
  | H15, governance | mapped-ckpt leaves less revoked state resident than native | mean resident pages per revoked tenant, 5 s after authority ends |

- **Threshold:** α = 0.05 / 3 ≈ 0.0167 per hypothesis (Bonferroni).
  Confirmed only if the direction favours mapped-ckpt and p < 0.0167;
  with all 15 seeds usable, that needs at least 13 of 15.
- **Reported, not deciding:**
  - within-deadline share and p99;
  - swap I/O and request-thread major faults;
  - checkpoint count, pages and latency, and whether digests are
    identical across arms;
  - integrity, and warm-up kills.

### Result: run `20261011T181835Z`

The run followed the pre-registration exactly.
- 43 of 45 runs completed, all with 0 integrity failures and 0 authority
  violations.
- Native was OOM-killed in warm-up on seeds 701 and 715; mapped and
  mapped-ckpt were never killed.

| Hypothesis | Pairs | mapped-ckpt better / worse / ties | Sign test p | Outcome | Medians |
|---|---|---|---|---|---|
| H13: fewer missed interactive deadlines than native | 13 | 8 / 5 / 0 | 0.581 | **not confirmed** | 7 vs 10 |
| H14: fewer missed interactive deadlines than mapped | 15 | 6 / 8 / 1 | 0.791 | **not confirmed** | 10 vs 8 |
| **H15: less revoked state resident 5 s after authority ends than native** | 13 | **13 / 0 / 0** | **0.0002** | **confirmed** | **1.96 vs 5.75 pages** |

| Arm | Completed | Interactive ≤100 ms, mean (min) | p99 median (max), ms | Revoked tenants with nothing resident at 1 s / 5 s (median) | Swap-ins, median | Swap-outs, median | Warm-up swap-outs, median |
|---|---|---|---|---|---|---|---|
| native | 13 | 99.67% (98.20%) | 4.3 (355) | 4% / 9% | 8,901 | 8,127 | 78,840 |
| mapped | 15 | 99.88% (99.53%) | 3.5 (9.9) | 4% / 9% | 8,318 | 7,337 | 2,445 |
| mapped-ckpt | 15 | 99.85% (99.68%) | 8.5 (49.7) | **54% / 89%** | 10,940 | 6,498 | 2,223 |

**Checkpoints:** about 429 per run (median), writing about 5,546 pages;
p50 3.3 ms, max 110 ms (fdatasync on the request thread).

**Observed, not pre-registered, so no claim:**
- Native collapsed on two seeds: 167 misses (p99 355 ms) and 112.
- Neither mapped arm went below 99.5% on any seed. The worst seeds were
  mapped 38 misses and mapped-ckpt 29.
- Pooled over today's runs at 16 MiB, native's warm-up kills keep
  recurring, while the mapped arms have had none. No single pre-registered
  test has had the power to confirm that.

## VRE-1.5 final verdict

**The performance thesis fails, as measured.**
- No envelope-aware policy beat native on interactive deadlines in a
  pre-registered test.
- That held for every approach tried:
  - snapshot demotion;
  - madvise steering;
  - background headroom;
  - working-set prefetch;
  - the mapped state model;
  - checkpoint on revocation.
- On this host and workload, the information the envelope carries is
  largely redundant with recency (oracle bound). Native's tail collapses
  are run-to-run random in severity, so 10–15 seeds cannot confirm a small
  advantage.

**The governance thesis is confirmed, and now measured rather than assumed:**

| | Result | Where shown |
|---|---|---|
| Authority | enforced exactly: 0 authority violations | every run in this directory |
| Integrity | state preserved exactly: 0 integrity failures in every completed run | every run, including runs where state is checkpointed and remapped |
| **Residency follows authority (H15)** | with checkpoint on revocation, a revoked tenant's state leaves memory: 89% of revoked tenants have nothing resident 5 s after authority ends, against 9% under native (13/0, p = 0.0002) | pre-registered H15 |
| Evidence | each revocation produces a deterministic checkpoint digest of exactly the state modified under that authority: all 415 digests in the same order across 4 runs of seed 402 (3 repeats, plus a rerun through the `revoke.py` host) | `20261011T180001Z` and the rerun below |

**Measured mechanism facts, holding in every run (not deadline claims):**
- Mapping the governed snapshot instead of copying it cuts warm-up swap
  writes about 35-fold.
- Native's collapse is triggered by a known trace event: several hot
  tenants' authority windows opening together.

**What survives into implementation:** checkpoint on revocation, as a
governance mechanism and not a performance one.
- `vre15/revoke.py` is the mechanism as a library: `map_image`, `revoke`
  (returns a `Receipt`), `modified_pages` and `held_privately`.
  `host.py`'s mapped-ckpt policy now calls it, so there is one
  implementation.
- The invariant is tested in `tests/test_vre15_revoke.py` on real
  mappings, without privilege. After `revoke`:
  - no page of the region is present in or swapped from the process;
  - the image holds exactly the state;
  - the state faults back unchanged.
  The tests also check that:
  - the digest is deterministic and covers both page position and
    content;
  - each authority window is receipted separately;
  - a page already in swap is written back and leaves swap (this test
    needs an active swap device and skips without one);
  - mismatched or unaligned regions are refused.
- Page-cache residency after revocation is best-effort, not invariant.
  The invariant tested is the page-table one. Page-cache residency is the
  H15 measurement.
- **Refactor check:** seed 402, 16 MiB, mapped-ckpt, rerun through the
  refactored host (local run `20261011T194553Z`, not committed):
  - 415 checkpoints, digest sequence identical to `20261011T180001Z`;
  - 0 integrity failures, 0 authority violations;
  - revoked tenants with nothing resident: 53% at 1 s, 91% at 5 s.
- Measured costs, recorded with the mechanism:
  - more swap-ins when revoked tenants return;
  - a longer p99 while fdatasync runs on the request thread.
- Not yet shown on WSL2 / cgroup v2, where production runs. This host is
  a cgroup v1 VM. The mechanism uses only mmap, madvise and pagemap, none
  of which depends on the cgroup version, but that is unverified until
  run there.

### Still untested

- Workloads that touch most of their state per request, and authority gaps
  long enough for swap to matter more than reclaim.
- Other hosts. The noise above may be specific to this VM's storage and
  reclaim behaviour.
