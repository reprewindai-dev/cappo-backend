#!/usr/bin/env bash
# Pre-registered transfer test of VRE-1.5 to this machine (WSL2 / cgroup v2).
# The hypotheses are fixed in vre15/README.md, "Pre-registered: transfer to
# WSL2 / cgroup v2", committed before any run with these seeds.
#
#   scripts/vre15_check.sh             # once, as yourself: sets up .venv
#   sudo scripts/vre15_transfer.sh     # about 60-90 minutes; keep the machine awake
#
# Run it on a machine that serves nothing. It drops the page cache for the
# whole kernel before each of its 30 runs, switches a 2 GiB swap file on and
# off, and keeps memory under pressure for over an hour. On Windows, every
# WSL distro shares one VM with Docker Desktop, so on a PC whose Docker serves
# production it would hit production. It refuses to run under WSL unless
# given --shared-vm-ok.
#
# Steps:
#   1. selftest: the kernel enforces the memory contract the runs rely on
#      (budget, OOM kill counted, swap allowed or forbidden);
#   2. 30 runs: native and mapped-ckpt (with signed evidence), 16 MiB,
#      seeds 801-815, 300 tenants, 90 s each;
#   3. the pre-registered verdict, printed as one JSON line to paste back,
#      and saved as transfer-summary.json in the run directory.
set -euo pipefail

cd "$(dirname "$0")/.."
py=.venv/bin/python
if [[ $EUID -ne 0 ]]; then
  echo "needs root (cgroups, swap, page cache): sudo $0" >&2
  exit 2
fi
if [[ ! -x "$py" ]]; then
  echo "run scripts/vre15_check.sh first (as yourself, without sudo): it sets up .venv" >&2
  exit 2
fi
if grep -qi microsoft /proc/sys/kernel/osrelease && [[ "${1:-}" != "--shared-vm-ok" ]]; then
  cat >&2 <<'MSG'
Refusing to run under WSL.

Every WSL distro on a Windows PC shares one Linux VM, including Docker
Desktop's. This run drops the page cache for that whole VM 30 times, swaps
a 2 GiB file on and off, and keeps memory under pressure for over an hour.
If Docker on this PC serves anything live, this run would degrade it.

Run it on a separate Linux machine with cgroup v2 (a spare box or a small
cloud VM). Only if nothing on this PC's Docker matters right now:
  sudo scripts/vre15_transfer.sh --shared-vm-ok
MSG
  exit 2
fi

echo "== 1/3 selftest: memory cgroup contract on this kernel"
"$py" -m vre15.cgroups --selftest | tee /tmp/vre15-selftest.json
if ! "$py" -c 'import json,sys; sys.exit(0 if json.load(open("/tmp/vre15-selftest.json"))["ok"] else 1)'; then
  echo "selftest failed: paste the JSON above back; nothing else was run" >&2
  exit 1
fi

echo "== 2/3 the pre-registered runs (30 runs, roughly 60-90 minutes)"
log="$(mktemp /tmp/vre15-transfer.XXXXXX.log)"
"$py" -m vre15.runner --budget-mib 16 --variants informative \
  --seed 801 802 803 804 805 806 807 808 809 810 811 812 813 814 815 \
  --policies native mapped-ckpt --rotate-order --probe-revoked --record-ws --sample --evidence \
  --out vre15/runs 2>&1 | tee "$log"
run_dir="$(sed -n 's/^run written to //p' "$log" | tail -1)"
if [[ -z "$run_dir" ]]; then
  echo "the run did not finish; the log is $log" >&2
  exit 1
fi

echo "== 3/3 verdict"
"$py" -m vre15.transfer "$run_dir/manifest.json" | tee "$run_dir/transfer-summary.json"
if [[ -n "${SUDO_USER:-}" ]]; then
  chown -R "$SUDO_USER" "$run_dir"
fi
echo
echo "Done. Paste the JSON line above back. Full results: $run_dir"
