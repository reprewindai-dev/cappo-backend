#!/usr/bin/env bash
set -e
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

echo "Current user: $(whoami)"
echo "--- Testing root moving process into /sys/fs/cgroup/vre0/subjob1 ---"
mkdir -p /sys/fs/cgroup/vre0/subjob1
echo "104857600" > /sys/fs/cgroup/vre0/subjob1/memory.max
echo $$ > /sys/fs/cgroup/vre0/subjob1/cgroup.procs
echo "Self cgroup: $(cat /proc/self/cgroup)"
echo "Current memory: $(cat /sys/fs/cgroup/vre0/subjob1/memory.current) bytes"

# Move back to root cgroup /init.scope
echo $$ > /sys/fs/cgroup/init.scope/cgroup.procs || echo $$ > /sys/fs/cgroup/cgroup.procs || true
rmdir /sys/fs/cgroup/vre0/subjob1
rmdir /sys/fs/cgroup/vre0 || true
echo "SUCCESS!"
