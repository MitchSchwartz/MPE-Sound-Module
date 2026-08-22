#!/bin/bash
# C0 gate — offline instrument conformance (target ≤ 15 min wall clock).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"
START=$SECONDS

echo "=== instrument-conformance $(date -Is) ==="

bash "${ROOT}/tests/test_instrument_conformance.sh"
bash "${ROOT}/tests/test_meter_harness.sh"

# Unit tests that touch measurement / meter paths (no direct python3 discover — project rule)
if [ -x "${ROOT}/.venv/bin/python" ]; then
    "${ROOT}/.venv/bin/python" -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q 2>/dev/null \
        || python3 -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q
else
    python3 -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q
fi

ELAPSED=$((SECONDS - START))
echo "conformance wall_time_s=${ELAPSED}"
if [ "$ELAPSED" -gt 900 ]; then
    echo "WARNING: conformance exceeded 15 min (${ELAPSED}s) — gate too slow to trust" >&2
    exit 1
fi

echo "SENTINEL conformance-pass"
exit 0
