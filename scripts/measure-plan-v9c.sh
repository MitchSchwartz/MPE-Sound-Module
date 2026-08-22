#!/bin/bash
# V9-c follow-ups — regression + ceiling search + Crystals @ 512 ramp.
#
# Usage: sudo ./scripts/measure-plan-v9c.sh [--artifact-dir DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
ARTIFACT_DIR="${USER_HOME}/plan-v9c-$(date +%Y%m%d-%H%M%S)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"

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
exec > >(tee -a "${ARTIFACT_DIR}/plan-v9c.log") 2>&1

echo "=== Plan V9-c $(date -Is) artifacts=$ARTIFACT_DIR ==="
echo "git=$(git -C "$SCRIPT_DIR/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"

echo ""
echo "=== V9-c1 Cloud Horn @ 7 voices × 60 s (V8-b regression) ==="
V9C1="${ARTIFACT_DIR}/v9c1-cloudhorn-7.log"
"$SCRIPT_DIR/measure-confirm-at-voices.sh" \
    --patch-name "Cloud Horn" --voices 7 --seconds 60 \
    --output "$V9C1" --tag V9c1-cloudhorn-7

echo ""
echo "=== V9-c2 Closed Hat ceiling search (binary, hi=12, 60 s) ==="
"$SCRIPT_DIR/measure-ceiling-search.sh" \
    --patch-name "Closed Hat" --hi 12 --seconds 60 \
    --artifact-dir "${ARTIFACT_DIR}/closed-hat-ceiling"

echo ""
echo "=== V9-c3 Crystals @ 512×3 ramp (V8 review candidate) ==="
V9C3="${ARTIFACT_DIR}/v9c3-crystals-512-ramp.log"
: >"$V9C3"
"$SCRIPT_DIR/measure-capacity-ramp.sh" \
    --buffer 512 --periods 3 --tag V9c3-Crystals-512 \
    --output "$V9C3" --patch-name Crystals \
    --patch-path "${QUICK_SELECT}/Crystals.fxp" \
    --probe-sec 8 --skip-confirm --start-voice 1

echo "SENTINEL v9c-complete artifacts=$ARTIFACT_DIR"
