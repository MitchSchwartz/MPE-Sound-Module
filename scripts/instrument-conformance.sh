#!/bin/bash
# C0 gate — offline parser conformance + live instrument checks (target ≤ 15 min).
#
# Usage:
#   instrument-conformance.sh           # both halves (full gate; requires Pi for live)
#   instrument-conformance.sh --offline # parser/fixture suite only
#   instrument-conformance.sh --live    # meter + Pi positive controls only
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"
START=$SECONDS
MODE="${1:-all}"

run_offline() {
    echo "=== instrument-conformance OFFLINE $(date -Is) ==="
    bash "${ROOT}/tests/test_instrument_conformance_offline.sh"
    bash "${ROOT}/tests/test_meter_harness.sh"

    if [ -x "${ROOT}/.venv/bin/python" ]; then
        "${ROOT}/.venv/bin/python" -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q 2>/dev/null \
            || python3 -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q
    else
        python3 -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q
    fi
}

run_live() {
    echo "=== instrument-conformance LIVE $(date -Is) ==="
    bash "${ROOT}/tests/test_instrument_conformance_live.sh"
    bash "${ROOT}/scripts/measure-instrument-conformance-live.sh"
}

case "$MODE" in
    --offline)
        run_offline
        ;;
    --live)
        run_live
        ;;
    --help|-h)
        echo "Usage: $0 [--offline|--live]"
        echo "  (no args)  Run offline + live (full C0 gate)"
        exit 0
        ;;
    all|"")
        run_offline
        run_live
        ;;
    *)
        echo "ERROR: unknown mode: ${MODE}" >&2
        echo "Usage: $0 [--offline|--live]" >&2
        exit 2
        ;;
esac

ELAPSED=$((SECONDS - START))
echo "conformance wall_time_s=${ELAPSED}"
if [ "$ELAPSED" -gt 900 ]; then
    echo "WARNING: conformance exceeded 15 min (${ELAPSED}s) — gate too slow to trust" >&2
    exit 1
fi

echo "SENTINEL conformance-pass mode=${MODE}"
exit 0
