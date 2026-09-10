#!/usr/bin/env bash
# run_vre0.sh — Launch VRE-0 supervisor inside a systemd --user delegated unit
#
# Usage: bash run_vre0.sh [--dry-run]
#
# This script:
#   1. Creates a transient systemd --user unit with Delegate=yes, MemoryMax=256M
#   2. Inside that unit, runs the VRE-0 supervisor (supervisor.py)
#   3. The supervisor performs topology setup, runs 4 scenarios, emits receipts
#
# Requirements:
#   - WSL2 with systemd enabled (WSL >= 0.67.6, wsl.conf: [boot] systemd=true)
#   - /home/antho/vre0-venv with wasmtime installed
#   - cappo-backend/vre0/{envelope.py,supervisor.py,wasm_modules/*.wat}

set -euo pipefail

CAPPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="/home/antho/vre0-venv/bin/python3"
SUPERVISOR="$CAPPO_ROOT/vre0/supervisor.py"
UNIT_NAME="vre0-supervisor-$(date +%s)"

# Validate prerequisites
if [ ! -f "$VENV_PYTHON" ]; then
    echo "FATAL: $VENV_PYTHON not found. Run:"
    echo "  python3 -m venv /home/antho/vre0-venv"
    echo "  /home/antho/vre0-venv/bin/pip install wasmtime"
    exit 1
fi

if [ ! -f "$SUPERVISOR" ]; then
    echo "FATAL: $SUPERVISOR not found"
    exit 1
fi

if ! "$VENV_PYTHON" -c "import wasmtime" 2>/dev/null; then
    echo "FATAL: wasmtime not importable in $VENV_PYTHON"
    exit 1
fi

if [ "${1:-}" = "--dry-run" ]; then
    echo "[dry-run] would launch: $UNIT_NAME"
    echo "  venv:        $VENV_PYTHON"
    echo "  supervisor:  $SUPERVISOR"
    exit 0
fi

echo "============================================================"
echo "  VRE-0 LAUNCH"
echo "  unit:        $UNIT_NAME"
echo "  supervisor:  $SUPERVISOR"
echo "  python:      $VENV_PYTHON"
echo "============================================================"

# Check if systemd --user is available
if ! systemctl --user status >/dev/null 2>&1; then
    echo ""
    echo "WARNING: systemd --user not available."
    echo "  This means we cannot create a delegated cgroup unit."
    echo "  Attempting direct run (supervisor will use current cgroup)..."
    echo ""
    # Try running directly — supervisor.py will use whatever cgroup we are in
    exec "$VENV_PYTHON" "$SUPERVISOR"
fi

# Launch inside a delegated systemd --user transient unit
# --wait      : block until the unit exits
# --pty       : allocate a pseudo-terminal so stdout is visible
# ExecStart must be quoted carefully for systemd-run
systemd-run \
    --user \
    --unit="$UNIT_NAME" \
    --wait \
    --pty \
    --property=Delegate=yes \
    --property=MemoryMax=256M \
    --property=CPUWeight=100 \
    --property=Description="VRE-0 Supervisor — governed execution proof" \
    --setenv=PYTHONPATH="$CAPPO_ROOT" \
    "$VENV_PYTHON" "$SUPERVISOR"

echo ""
echo "VRE-0 complete. Receipts: $CAPPO_ROOT/vre0/receipts/"
