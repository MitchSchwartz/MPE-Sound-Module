#!/bin/bash
# V10-b validation — Closed Hat @ 15 voices × 8 s ramp probe must not read clean.
#
# Usage: sudo ./scripts/measure-v10b-validate.sh [--artifact-dir DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
ARTIFACT_DIR="${USER_HOME}/v10b-validate-$(date +%Y%m%d-%H%M%S)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
PROBE_SEC=8
VOICES=15

while [ $# -gt 0 ]; do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,5p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
LOG="${ARTIFACT_DIR}/v10b-closed-hat-15.log"
: >"$LOG"

echo "=== V10-b validate Closed Hat @ ${VOICES} × ${PROBE_SEC}s ==="
"$SCRIPT_DIR/measure-capacity-ramp.sh" \
    --buffer 1024 --periods 3 \
    --tag V10b-ClosedHat-15 \
    --output "$LOG" \
    --patch-name "Closed Hat" \
    --patch-path "${QUICK_SELECT}/Closed Hat.fxp" \
    --probe-sec "$PROBE_SEC" --skip-confirm \
    --start-voice "$VOICES" --max-voices "$VOICES" 2>&1 | tee "${ARTIFACT_DIR}/v10b-validate.log"

xr="$(awk '/PROBE voices=15 / { split($0, a, "xruns_delta="); print a[2]+0 }' "$LOG")"
echo "PARSED xruns_delta=${xr}"

if [ "$xr" -eq 0 ]; then
    echo "FAIL: V10-b probe still reads clean @ 15 voices (expected >>0; V9-c confirm had 275 @ 8s)" >&2
    exit 1
fi

echo "PASS: V10-b probe xruns_delta=${xr} @ 15 voices (non-zero)"
echo "SENTINEL v10b-validate-pass artifacts=${ARTIFACT_DIR}"
