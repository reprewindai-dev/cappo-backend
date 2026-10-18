#!/usr/bin/env bash
set -e
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

echo "--- 1. Creating /sys/fs/cgroup/vre0 as root ---"
mkdir -p /sys/fs/cgroup/vre0
echo "+memory +cpu" > /sys/fs/cgroup/vre0/cgroup.subtree_control

echo "--- 2. Delegating ownership to antho ---"
# In cgroup v2 delegation:
# The user needs write access to cgroup.procs, cgroup.subtree_control, and directory
chown -R antho:antho /sys/fs/cgroup/vre0
ls -ld /sys/fs/cgroup/vre0
ls -l /sys/fs/cgroup/vre0/cgroup.procs /sys/fs/cgroup/vre0/cgroup.subtree_control
