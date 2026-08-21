#!/usr/bin/env bash
# Load N fixture clips into the running SooperLooper engine and trigger playback.
# Used by measure-loop-curve.sh (T4 / E3). loops=0 is a no-op (idle engine).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLIPS_DIR="${MPE_SL_TEST_CLIPS:-${REPO_ROOT}/tests/fixtures/sooperlooper-loops}"
OSC_HOST="${MPE_SL_OSC_HOST:-127.0.0.1}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
LOOPS="${1:-0}"

if [ "$LOOPS" = "0" ]; then
    echo "load-n-loops: 0 — idle engine (no clips loaded)"
    exit 0
fi

case "$LOOPS" in
    4 | 8 | 16) ;;
    *)
        echo "load-n-loops: loops must be 0, 4, 8, or 16 (got $LOOPS)" >&2
        exit 2
        ;;
esac

command -v oscsend >/dev/null 2>&1 || {
    echo "load-n-loops: oscsend required" >&2
    exit 1
}

if [ ! -f "${CLIPS_DIR}/loop00.wav" ]; then
    bash "${SCRIPT_DIR}/generate-test-clips.sh" "${CLIPS_DIR}"
fi

last=$((LOOPS - 1))
for i in $(seq 0 "$last"); do
    wav="${CLIPS_DIR}/loop$(printf '%02d' "${i}").wav"
    [ -f "$wav" ] || {
        echo "load-n-loops: missing $wav" >&2
        exit 1
    }
    oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/load_loop" sss "${wav}" "" ""
    sleep 0.12
done
for i in $(seq 0 "$last"); do
    oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/hit" s trigger
done
echo "load-n-loops: loaded and triggered loops 0..${last}"
sleep 2
