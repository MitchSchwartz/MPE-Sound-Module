#!/bin/bash
# T4c — finish 1024 loop curve: loops8 + loops16 only (condition B).
#
# Usage:
#   sudo ./scripts/measure-loop-curve-1024-finish.sh [--runs 15] [--output FILE]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUNS=15
OUTPUT="${MPE_LOOP_CURVE_1024_LOG:-$HOME/loop-curve-1024-finish.log}"
BUF=1024
LOOPS_LIST=(8 16)

while [ $# -gt 0 ]; do
    case "$1" in
        --runs) RUNS="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

{
    echo
    echo "=== measure-loop-curve-1024-finish runs=${RUNS} $(date -Is) ==="
    echo "SENTINEL curve-1024-start"
} >>"$OUTPUT"

"${SCRIPT_DIR}/set-surge-audio.sh" --buffer "$BUF"
sleep 8

for loops in "${LOOPS_LIST[@]}"; do
    tag="buf${BUF}-loops${loops}"
    echo "=== ${tag} ==="
    echo "=== block ${tag} $(date -Is) ===" >>"$OUTPUT"

    if ! "${SCRIPT_DIR}/measure-latency-run.sh" \
        --buffer "$BUF" \
        --condition B \
        --runs "$RUNS" \
        --playing-loops "$loops" \
        --no-restore-buffer \
        --output "$OUTPUT"; then
        echo "ERROR: measure failed for ${tag}" >&2
        exit 1
    fi
done

echo "SENTINEL curve-1024-complete" | tee -a "$OUTPUT"
echo "Appended to $OUTPUT"
