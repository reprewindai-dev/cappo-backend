#!/usr/bin/env python3
"""
RAM bomb subprocess worker.
Allocates and touches memory in 1 MiB chunks until OOM or completion.
Intended to be run as a child process by the VRE-0 supervisor,
with its PID placed in an execution cgroup leaf limited to 64 MiB.
The cgroup OOM killer terminates this process; the supervisor observes
memory.events for oom_kill_delta.
"""
import sys

CHUNK_SIZE = 1024 * 1024  # 1 MiB
TARGET_MIB = 200          # 200 MiB — well beyond the 64 MiB cgroup limit

chunks = []
for i in range(TARGET_MIB):
    chunk = bytearray(CHUNK_SIZE)
    # Touch every page to force physical RSS
    for j in range(0, CHUNK_SIZE, 4096):
        chunk[j] = 0xFF
    chunks.append(chunk)

# If we get here, OOM was not triggered (unexpected)
print("RAM_BOMB_COMPLETED_UNEXPECTEDLY", flush=True)
sys.exit(1)
