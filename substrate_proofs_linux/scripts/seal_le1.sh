#!/usr/bin/env bash
set -euo pipefail

PROBE_C=/mnt/c/Users/antho/.gemini/antigravity/scratch/linux-vre-probes/probes/linux_evidence_1/linux_evidence_1.bpf.c
PROBE_PY=/mnt/c/Users/antho/.gemini/antigravity/scratch/linux-vre-probes/probes/linux_evidence_1/linux_evidence_1.py
OUT=/tmp/le1_seal_run.json

echo "=== PROBE SHA-256 ==="
sha256sum "$PROBE_C"
sha256sum "$PROBE_PY"

echo "=== ENVIRONMENT ==="
python3 --version
uname -r
uname -m
cat /etc/os-release | grep PRETTY_NAME

echo "=== CANONICAL RUN ==="
# Capture stdout but let stderr go to console for debugging if needed
python3 "$PROBE_PY" > "$OUT" 2>&1
echo "Run exit code: $?"

echo "=== RAW RESULT SHA-256 ==="
sha256sum "$OUT"

echo "=== PROVENANCE FIELDS ==="
python3 - <<'PYEOF'
import json, hashlib, sys

with open("/tmp/le1_seal_run.json") as f:
    raw = f.read()

digest = hashlib.sha256(raw.encode()).hexdigest()
print(f"result_sha256:  {digest}")

# Multiple JSON objects in the file
results = []
for block in raw.split('}'):
    if '{' in block:
        try:
            results.append(json.loads(block[block.find('{'):] + '}'))
        except:
            pass

for r in results:
    print(f"{r.get('proof', 'Unknown')}: {r.get('verdict', 'Unknown')} (seen={r.get('seen_total')}, submitted={r.get('submitted_total')}, drops={r.get('reserve_failures')}, delivered={r.get('delivered_to_userspace')})")
PYEOF
