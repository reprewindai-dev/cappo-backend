#!/usr/bin/env bash
set -euo pipefail

PROBE=/mnt/c/Users/antho/.gemini/antigravity/scratch/linux-vre-probes/probes/linux_binding_0.py
OUT=/tmp/lb0_seal_run.json

echo "=== PROBE SHA-256 ==="
sha256sum "$PROBE"

echo "=== ENVIRONMENT ==="
python3 --version
uname -r
uname -m
cat /etc/os-release | grep PRETTY_NAME

echo "=== CANONICAL RUN ==="
python3 "$PROBE" > "$OUT" 2>&1
echo "Run exit code: $?"

echo "=== RAW RESULT SHA-256 ==="
sha256sum "$OUT"

echo "=== PROVENANCE FIELDS ==="
python3 - <<'PYEOF'
import json, hashlib, subprocess, sys

with open("/tmp/lb0_seal_run.json") as f:
    raw = f.read()
    data = json.loads(raw)

digest = hashlib.sha256(raw.encode()).hexdigest()
print(f"result_sha256:  {digest}")
print(f"session_id:     {data['session_id']}")
print(f"verdict:        {data['verdict']}")
print(f"boot_id:        {data['boot_id']}")
print(f"kernel:         {data['kernel'].split()[2]}")

pass_summary = data.get('pass_summary', {})
for k,v in pass_summary.items():
    print(f"  {k}: {v}")
PYEOF
