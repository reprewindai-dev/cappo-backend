# Evidence-mode run 1 (format v0, superseded): kept for its cost result

The first run with `--evidence`: seed 402, 16 MiB, mapped-ckpt.

**Evidence: valid.** At run time the bundle verified VALID (W1):
- 415 revocations across 300 tenants;
- all 300 images checked;
- `held_privately` was 0 every time.

The verdict is in `manifest.json`. The checkpoint digest sequence is
identical to `20261011T180001Z`.

**Cost: too high.** In this format the runtime hashed each tenant's whole
image (4 MiB) at every revocation, on the request thread, inside the
16 MiB cgroup. Reading the images through the page cache evicted live
tenants:

| Measure | This run | Same seed, no evidence |
|---|---|---|
| Interactive deadlines met | 90.94% | 99.89% |
| p99 | 961 ms | 7 ms |
| Swap-ins | 90,538 | 13,059 |

Evidence cost per record was 19.8 ms p50 and 806 ms at worst.

**The fix that followed:**
- The runtime commits only to what it wrote: the page runs and the receipt
  digest.
- The witness, outside the cgroup, hashes the final images into its
  checkpoint.
- Signing and fsync run on a background writer thread.

The bundle here is in the superseded v0 format: its envelopes commit to a
runtime image hash. The current verifier rejects it as
REJECTED_ENVELOPE_MISMATCH, so it is renamed `*.evidence-v0.json`. It is
not deleted.
