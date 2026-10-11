# VRE-1A / 1B: baseline materialization and dormant population

Measured on one host. These are not universal numbers. Run
`20261011T051429Z`: Linux 6.18 Firecracker VM, 4 vCPU, 16 GiB, Python
3.13.16, wasmtime-py 44.0.0. Raw samples are in
`runs/20261011T051429Z/samples.jsonl`; summaries are in its `manifest.json`.

**Mechanism.** A snapshot of a Wasm guest's linear memory plus its mutable
globals, taken and restored inside wasmtime. It is not a VM snapshot
(Firecracker) or a process snapshot (CRIU); this host has no KVM and no CRIU.
It measures the cost of materializing guest state. It does not measure VM
boot or a host-level restore.

**Integrity.** Every restore runs in a fresh process, then serves one
request. The resulting digest and memory SHA-256 must equal what the
original instance produced after the same request. All 740 restores passed
(540 for VRE-1A, 200 for VRE-1B), with **0 integrity failures**.

## Run it

```bash
pip install -r vre1/requirements.txt
sudo python -m vre1.bench --reps 10 --tenants 500 --samples 40   # root: drops the page cache for cold restores
pytest tests/test_vre1_snapshot.py
```

## How it was measured

- **Workload** (`workloads/stateful.wat`): state is a set of 4 KiB blocks,
  each either pseudo-random or zero, at a set density. Requests rewrite
  words in a 64 KiB hot set.
- **The data is pseudo-random, so compression is shown at its worst case.**
  Real state usually compresses better than this.
- **Codecs:**
  - `raw`: every byte;
  - `sparse`: a bitmap plus the non-zero blocks only;
  - `sparse-zlib1`: sparse, then zlib level 1.

  Restore copies each non-zero block once, straight into guest memory.
- **Restore timing** runs from "go" to state restored. It covers read,
  SHA-256 verify, decode, loading the precompiled module, instantiate, and
  writing memory. Python start-up is excluded; the host process is already
  running.
  - **cold**: the page cache was dropped just before;
  - **warm**: the snapshot file was already cached.
- **Resident memory:**
  - *settled*: the process's resident memory after the restore, once
    transient buffers are freed;
  - *peak*: its high-water mark, including those buffers;
  - *hot*: the live instance's resident memory before the snapshot.
- **Samples:** 10 reps per cell in VRE-1A and 40 per population in VRE-1B.
  With n = 10, p95 is the maximum.

## VRE-1A: one stateful workload

Cold restore p50 / p95 in ms, snapshot size, and settled resident memory
after restore:

| Logical state | Density | raw | sparse | sparse-zlib1 |
|---|---|---|---|---|
| 1 MiB | 5% | 16.0 / 18.5, 1.05 MB, 3.1 MiB | 9.8 / 11.3, 0.12 MB, 2.3 MiB | 9.9 / 10.6, 0.06 MB, 2.6 MiB |
| 1 MiB | 100% | 15.8 / 17.5, 1.05 MB, 3.1 MiB | 15.5 / 23.8, 1.05 MB, 3.1 MiB | 20.5 / 23.8, 1.05 MB, 4.3 MiB |
| 8 MiB | 5% | 51.7 / 65.6, 8.4 MB, 10.1 MiB | **14.8 / 16.8, 0.51 MB, 2.6 MiB** | 15.3 / 19.7, 0.46 MB, 3.2 MiB |
| 8 MiB | 25% | 52.0 / 67.4, 8.4 MB, 10.1 MiB | 23.6 / 25.6, 2.1 MB, 4.2 MiB | 29.7 / 35.4, 2.1 MB, 6.4 MiB |
| 8 MiB | 100% | 53.7 / 70.0, 8.4 MB, 10.1 MiB | 51.5 / 56.0, 8.4 MB, 10.1 MiB | 76.7 / 86.7, 8.4 MB, 18.3 MiB |
| 32 MiB | 5% | 174.2 / 245.6, 33.6 MB, 34.1 MiB | **22.8 / 28.0, 1.7 MB, 3.8 MiB** | 26.0 / 35.9, 1.6 MB, 5.6 MiB |
| 32 MiB | 25% | 173.4 / 255.6, 33.6 MB, 34.1 MiB | 54.8 / 65.7, 8.3 MB, 10.5 MiB | 79.2 / 90.7, 8.3 MB, 18.6 MiB |
| 32 MiB | 100% | 174.7 / 207.4, 33.6 MB, 34.1 MiB | 175.8 / 251.5, 33.6 MB, 34.1 MiB | 286.7 / 306.2, 33.6 MB, 34.6 MiB |

Every cell, warm runs and phase timings included, is in `manifest.json`.

What the measurements say:

1. **Restore cost follows the non-zero state, not the logical size.** With
   32 MiB logical at 5% density, the sparse format restores at a cold p50
   of 22.8 ms, against 174 ms for raw. That is 7.6× faster, from a snapshot
   19.9× smaller, with 3.8 MiB resident against 34 MiB.
2. **With dense state, sparse gains nothing and compression is a loss.** At
   100% density, zlib cannot shrink pseudo-random data (ratio 1.0). It adds
   about 105 ms of decode at 32 MiB and raises peak memory to 99 MiB,
   against 66 MiB for raw. Use compression only on state that is known to
   compress.
3. **Integrity verification is the largest fixed cost.** SHA-256 over the
   snapshot takes about 85 ms of the 174 ms raw 32 MiB restore (roughly
   0.4 GB/s on this host). Dropping it would be faster and would also give
   up the only check that the restored state is the state that was saved.
   A faster hash would cut this cost; that option has not been measured.
4. **The module is not the bottleneck.** Loading the precompiled module
   takes 0.4–8 ms, against about 3.5 ms to compile it from source.
   Instantiation takes under 5 ms.
5. **A cold page cache costs 10–25 ms** in most cells, which is the disk
   read. The first request after a cold restore takes about 1 ms, against
   0.2–0.3 ms warm.
6. **A live, idle instance is already lazily resident.** Wasm memory that
   the guest never touched is never committed. A live 32 MiB instance at 5%
   density held 2.1 MiB, less than the 3.8 MiB a restored copy holds (the
   restored copy also carries the runtime). Snapshotting a dormant tenant
   frees only the pages it actually touched, plus its process. It is not
   "logical size freed". This is the honest baseline that the VRE-1.5
   residency policy has to beat.

## VRE-1B: dormant population

There are 500 distinct tenants, each with 4 MiB logical state at 25%
density (sparse-zlib1, about 1.04 MB on disk each). For each population
size N, 40 tenants drawn at random from the first N were restored cold,
one at a time.

| N | Logical entitlement | On disk | Cold restore p50 / p95 / p99 (ms) | Peak resident, one restore | Logical ÷ peak resident | p95 ≤ 100 ms |
|---|---|---|---|---|---|---|
| 1 | 0.004 GiB | 1.05 MB | 20.2 / 27.1 / 86.8 | 6.0 MB | 0.7 | yes |
| 10 | 0.04 GiB | 10.5 MB | 19.4 / 29.6 / 29.9 | 6.1 MB | 6.8 | yes |
| 50 | 0.20 GiB | 52.2 MB | 20.4 / 28.9 / 29.5 | 6.2 MB | 33.6 | yes |
| 100 | 0.39 GiB | 103 MB | 19.1 / 25.0 / 26.9 | 6.3 MB | 67.1 | yes |
| 500 | 1.95 GiB | 521 MB | 20.6 / 28.7 / 32.9 | 6.3 MB | **331.4** | yes |

- **Cold restore latency did not depend on population size.** p95 stayed
  between 25 and 30 ms from 1 to 500 dormant tenants.
- **Read the ratio precisely.** It divides the logical state the population
  is entitled to by the resident memory of one restored tenant. It is that
  large only because tenants were materialized **one at a time**.
  - No concurrent restores, no simultaneously active tenants and no
    contention were tested.
  - The ratio says how much dormant entitlement this node can hold behind a
    28.7 ms cold p95. It does not say how many tenants it can serve at once.
  - The single p99 outlier at N=1 (86.8 ms) is one sample.

## Not measured yet

- **VRE-1C** (concurrent materialization) and **VRE-1D** (the collapse
  curve, with active working sets at 0.8× to 2× usable RAM). This host has
  no swap, so pushing past RAM gives OOM kills, not paging. A meaningful
  collapse curve needs either swap or zswap, or a residency scheduler that
  demotes tenants to snapshots, and that scheduler is VRE-1.5.
- **VM or process snapshots** (Firecracker, CRIU): not available on this
  host.
- **Real tenant data**: the pseudo-random state shows compression at its
  worst case.
- **VRE-1.5**: whether a policy that knows about the envelope (authority
  expiry, idle time, deadline class) beats native OS behaviour. Finding 6
  sets the bar: the runtime already keeps untouched memory off the books.
