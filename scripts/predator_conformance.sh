#!/usr/bin/env bash
set -e

echo "========================================================================"
echo "VEKLOM PREDATOR CONFORMANCE HARNESS"
echo "Target: CAPPO Backend Invariants"
echo "========================================================================"

# Activate the venv if we need to, but let's assume we run inside poetry/venv
# Generate machine-readable XML/JSON output for release gating
python3 -m pytest tests/adversarial/ -v -o junit_family=xunit2 --junitxml=predator_results.xml

echo ""
echo "[DONE] Predator conformance suite completed successfully."
echo "Results written to predator_results.xml"
