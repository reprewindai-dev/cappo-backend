#!/usr/bin/env bash
set -e

echo "=== 1. SYSTEM & DISTRO ==="
uname -a
cat /etc/os-release | grep -E 'PRETTY_NAME|VERSION_ID|ID='

echo ""
echo "=== 2. TOOLCHAIN & RUNTIMES ==="
for cmd in python3 pip pip3 gcc clang wabt wasm-tools wasmtime wat2wasm cargo rustc make; do
    p=$(type -p "$cmd" 2>/dev/null || echo "NOT_FOUND")
    echo "$cmd: $p"
done
python3 --version 2>&1 || true

echo ""
echo "=== 3. CGROUP V2 STATUS ==="
echo "--- cgroup mounts ---"
mount | grep cgroup || echo "No cgroup mounts found"
echo "--- filesystem type for /sys/fs/cgroup ---"
stat -f /sys/fs/cgroup || true
echo "--- /proc/self/cgroup ---"
cat /proc/self/cgroup || true
echo "--- cgroup.controllers in root ---"
cat /sys/fs/cgroup/cgroup.controllers 2>&1 || true
echo "--- cgroup.subtree_control in root ---"
cat /sys/fs/cgroup/cgroup.subtree_control 2>&1 || true

echo ""
echo "=== 4. SYSTEMD STATUS ==="
ps -p 1 -o pid,comm,args || true

echo ""
echo "=== 5. CGROUP CREATION & PERMISSION PROBE ==="
TEST_CGROUP="/sys/fs/cgroup/vre0_probe_test"
echo "Testing creation of $TEST_CGROUP as current user ($(whoami))..."
if mkdir "$TEST_CGROUP" 2>/dev/null; then
    echo "SUCCESS: Created $TEST_CGROUP"
    echo "Controllers in $TEST_CGROUP:"
    cat "$TEST_CGROUP/cgroup.controllers" 2>&1 || true
    echo "Subtree control in $TEST_CGROUP:"
    cat "$TEST_CGROUP/cgroup.subtree_control" 2>&1 || true
    rmdir "$TEST_CGROUP" 2>/dev/null || true
    echo "SUCCESS: Cleaned up $TEST_CGROUP"
else
    echo "FAILED: Cannot create $TEST_CGROUP directly as $(whoami)"
fi

echo ""
echo "=== 6. PYTHON ENVIRONMENT & VENV PROBE ==="
python3 -c "import sys; print('Python executable:', sys.executable); print('Python version:', sys.version)"
python3 -c "import venv; print('venv module available')" 2>&1 || echo "venv module NOT available"
