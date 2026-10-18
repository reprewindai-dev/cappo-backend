#!/usr/bin/env bash
set -e

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

echo "=== A. NON-ROOT PERMISSION CHECK ON ROOT CGROUP ==="
su - antho -c "mkdir /sys/fs/cgroup/nonroot_test 2>&1 || echo 'Cannot create cgroup as antho (expected)'"

echo ""
echo "=== B. CGROUP DELEGATION PROBE ==="
# Test creating /sys/fs/cgroup/vre0 as root and delegating to antho
mkdir -p /sys/fs/cgroup/vre0
echo "+memory +cpu" > /sys/fs/cgroup/vre0/cgroup.subtree_control || echo "Warning: failed to enable controllers on /sys/fs/cgroup/vre0"
chown -R antho:antho /sys/fs/cgroup/vre0
ls -ld /sys/fs/cgroup/vre0

echo "Testing operations as antho inside /sys/fs/cgroup/vre0..."
su - antho -c '
    set -e
    mkdir /sys/fs/cgroup/vre0/subjob1
    echo "104857600" > /sys/fs/cgroup/vre0/subjob1/memory.max
    cat /sys/fs/cgroup/vre0/subjob1/memory.max
    sh -c "echo \$\$ > /sys/fs/cgroup/vre0/subjob1/cgroup.procs && cat /proc/self/cgroup && cat /sys/fs/cgroup/vre0/subjob1/memory.current"
    rmdir /sys/fs/cgroup/vre0/subjob1
    echo "DELEGATION SUCCESSFUL!"
'
rmdir /sys/fs/cgroup/vre0 || true

echo ""
echo "=== C. SYSTEMD USER SLICE CHECK ==="
su - antho -c 'systemctl --user status 2>&1 || echo "systemctl --user not active or failed"'

echo ""
echo "=== D. APT REPOSITORIES & PACKAGES ==="
which apt-get || echo "apt-get not found"
apt-cache policy python3-pip wabt clang 2>&1 | head -n 30 || true

echo ""
echo "=== E. PYTHON 3.14 & PIP / WASMTIME PROBE ==="
python3 -m ensurepip --upgrade 2>&1 || echo "ensurepip failed"
