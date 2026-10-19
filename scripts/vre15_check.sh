#!/usr/bin/env bash
# Run the VRE-1.5 residency and evidence checks on any Linux machine
# (including WSL2). Root is not needed, except for --with-swap.
#
# On Windows, run it inside WSL (open "Ubuntu", or type `wsl` in PowerShell),
# from a clone in the Linux home directory, not in PowerShell.
#
#   scripts/vre15_check.sh              # set up .venv if needed, run the tests,
#                                       # verify the committed evidence bundle
#   sudo scripts/vre15_check.sh --with-swap
#                                       # also run the swap test, with a temporary
#                                       # 256 MiB swap file that is removed afterwards
#
# What runs:
#   1. tests/test_vre15.py, tests/test_vre15_revoke.py, tests/test_vre15_evidence.py,
#      tests/test_vre15_audit.py, tests/test_vre15_crash.py (a known defect, xfail)
#   2. vre15/verify_evidence.py on every committed evidence bundle, copied into an
#      empty directory with only its trust store, the way a third party would run it.
set -euo pipefail

cd "$(dirname "$0")/.."
with_swap=0
[[ "${1:-}" == "--with-swap" ]] && with_swap=1

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Linux only (WSL2 works). The residency tests need /proc/self/pagemap and MADV_PAGEOUT." >&2
  exit 2
fi

py=.venv/bin/python
if [[ ! -x "$py" ]]; then
  echo "== creating .venv and installing the project (one time)"
  if command -v uv >/dev/null; then
    uv venv -q --python 3.12 .venv            # uv downloads Python 3.12 if the system has none
    uv pip install -q -p "$py" -e ".[dev]" -r vre1/requirements.txt
  elif python3 -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    python3 -m venv .venv
    "$py" -m pip install -q -e ".[dev]" -r vre1/requirements.txt
  else
    echo "Needs Python 3.11+ with venv, and this system's python3 is $(python3 --version 2>&1)." >&2
    echo "Easiest fix (no sudo): install uv, then run this script again:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env" >&2
    exit 2
  fi
fi

swapfile=""
cleanup() {
  if [[ -n "$swapfile" ]]; then
    swapoff "$swapfile" 2>/dev/null || true
    rm -f "$swapfile"
  fi
}
trap cleanup EXIT
if (( with_swap )); then
  if [[ $EUID -ne 0 ]]; then
    echo "--with-swap needs root (swapon)" >&2
    exit 2
  fi
  swapfile="$(mktemp /var/tmp/vre15-swap.XXXXXX)"
  fallocate -l 256M "$swapfile"
  chmod 600 "$swapfile"
  mkswap "$swapfile" >/dev/null
  swapon "$swapfile"
  echo "== temporary swap file active: $swapfile"
fi

echo "== tests"
"$py" -m pytest -q -rs tests/test_vre15.py tests/test_vre15_revoke.py tests/test_vre15_evidence.py \
  tests/test_vre15_audit.py tests/test_vre15_crash.py

echo "== offline verification of committed evidence bundles"
status=0
for bundle in vre15/runs/*/*.evidence.json; do
  trust="${bundle%.evidence.json}.trust.json"
  dir="$(mktemp -d)"
  audit="${bundle%.evidence.json}.audit.jsonl"
  cp vre15/verify_evidence.py "$bundle" "$trust" "$dir/"
  extra=()
  if [[ -f "$audit" ]]; then
    cp "$audit" "$dir/"
    extra=(--audit "$(basename "$audit")" --require-audit)
  fi
  if (cd "$dir" && "$OLDPWD/$py" -I verify_evidence.py "$(basename "$bundle")" "$(basename "$trust")" \
        --require-witness ${extra[@]+"${extra[@]}"} > verdict.json); then
    "$py" - "$dir/verdict.json" "$bundle" <<'PY'
import json, sys
v = json.load(open(sys.argv[1]))
print(f"{sys.argv[2]}: {v['verdict']} {v['profile']}, {v['revocations']} revocations, "
      f"{v['tenants']} tenants, lag p50 {v['lag_ms_p50']} ms")
PY
  else
    echo "$bundle: REJECTED"; cat "$dir/verdict.json"; status=1
  fi
  rm -rf "$dir"
done
exit $status
