#!/usr/bin/env bash
set -e
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

echo "Current user: $(whoami)"
echo "--- 1. Testing mkdir /sys/fs/cgroup/vre0/subjob1 ---"
mkdir /sys/fs/cgroup/vre0/subjob1
echo "SUCCESS: Created /sys/fs/cgroup/vre0/subjob1"

echo "--- 2. Testing writing memory.max ---"
echo "104857600" > /sys/fs/cgroup/vre0/subjob1/memory.max
echo "memory.max is set to: $(cat /sys/fs/cgroup/vre0/subjob1/memory.max)"

echo "--- 3. Testing moving process into subjob1 ---"
echo $$ > /sys/fs/cgroup/vre0/subjob1/cgroup.procs
echo "Self cgroup: $(cat /proc/self/cgroup)"
echo "Current memory usage: $(cat /sys/fs/cgroup/vre0/subjob1/memory.current) bytes"

echo "--- 4. Moving process back to root or parent ---"
echo $$ > /sys/fs/cgroup/vre0/cgroup.procs || echo "Warning: cannot move back to vre0 if it has children and domain controllers"
echo "Self cgroup now: $(cat /proc/self/cgroup)"

echo "--- 5. Cleanup ---"
# To rmdir subjob1, it must have no processes
# If $$ is in subjob1, we can spawn a helper process to test
