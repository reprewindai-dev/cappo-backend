# Aborted smoke run: harness defect (kept as found)

Smoke test of mapped-ckpt vs mapped vs native (16 MiB, seed 402, 3 reps,
rotated order). Stopped by hand after two runs.

- Run 1, mapped-ckpt: completed; 0 integrity failures; 99.96% interactive
  within deadline. Checkpointing and remapping preserved every tenant's state.
- Run 2, mapped: **integrity failures 300 of 300.**

Cause: the runner built the page-aligned snapshot images once per invocation
and shared them across runs. mapped-ckpt writes each revoked tenant's modified
pages into its image (that is the design), so the next mapped run started from
the checkpointed, later state instead of the original snapshot, and the end-of-run
integrity check (state = original snapshot + this run's steps) correctly failed
for every tenant.

Fix (runner.py): every mapped / mapped-ckpt run gets its own sparse copy of the
pristine images, made outside the measured cgroup before the page-cache drop,
and removed with the run directory. The run was stopped at that point because
every later mapped run would have started from contaminated images. No manifest
was written; the per-request results and sampler files of the two runs are kept.
