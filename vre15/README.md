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

### Still untested

- Workloads that touch most of their state per request, and authority gaps
  long enough for swap to matter more than reclaim.
- Other hosts. The noise above may be specific to this VM's storage and
  reclaim behaviour.
