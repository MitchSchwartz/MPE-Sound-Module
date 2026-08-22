#!/bin/bash
# V8-b only — playable 1024×2 vs ×3 after V8-a survey.
#
# Usage: sudo ./scripts/measure-v8b-playable.sh --patch-name "Cloud Horn" --voices 7 \
#            --artifact-dir /home/mitch/plan-v8-YYYYMMDD-HHMMSS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
PATCH_NAME=""
VOICES=""
ARTIFACT_DIR=""
LOG_SLUG="redo"

while [ $# -gt 0 ]; do
    case "$1" in
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        --voices) VOICES="${2:?}"; shift 2 ;;
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        --log-slug) LOG_SLUG="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,6p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

[ -n "$PATCH_NAME" ] && [ -n "$VOICES" ] && [ -n "$ARTIFACT_DIR" ] || {
    echo "ERROR: --patch-name --voices --artifact-dir required" >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

PATCH_PATH="$QUICK_SELECT/${PATCH_NAME}.fxp"
[ -f "$PATCH_PATH" ] || { echo "ERROR: missing $PATCH_PATH" >&2; exit 1; }

mkdir -p "$ARTIFACT_DIR"
exec >>"${ARTIFACT_DIR}/plan-v8.log" 2>&1

echo ""
echo "=== V8-b redo patch=${PATCH_NAME} voices=${VOICES} $(date -Is) ==="
sudo -u "$RUN_AS_USER" python3 "$SCRIPT_DIR/load-patch-osc.py" "$PATCH_PATH"
sleep 1

V8B_X2="${ARTIFACT_DIR}/v8b-1024x2-${LOG_SLUG}.log"
"$SCRIPT_DIR/measure-latency-run.sh" \
    --buffer 1024 --periods 2 --condition A --runs 3 --seconds 45 \
    --hold-voices "$VOICES" --provenance-patch "$PATCH_NAME" --provenance-voices "$VOICES" \
    --output "$V8B_X2" --no-restore-buffer

V8B_X3="${ARTIFACT_DIR}/v8b-1024x3-${LOG_SLUG}.log"
"$SCRIPT_DIR/measure-latency-run.sh" \
    --buffer 1024 --periods 3 --condition A --runs 3 --seconds 45 \
    --hold-voices "$VOICES" --provenance-patch "$PATCH_NAME" --provenance-voices "$VOICES" \
    --output "$V8B_X3"

echo "SENTINEL v8b-redo-complete patch=${PATCH_NAME} voices=${VOICES}"
