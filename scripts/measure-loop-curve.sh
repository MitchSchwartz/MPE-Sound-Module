#!/bin/bash
# E3 / T4 — loop-count curve: xruns vs playing loops at 512 and 1024.
#
# Condition B (+ sooperlooper only). Appends to OUTPUT — never truncates.
#
# Usage:
#   sudo ./scripts/measure-loop-curve.sh [--runs 15] [--output FILE]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUNS=15
OUTPUT="${MPE_LOOP_CURVE_LOG:-$HOME/loop-curve-measure.log}"
LOOPS_LIST=(0 4 8 16)
BUFFERS=(512 1024)

while [ $# -gt 0 ]; do
    case "$1" in
        --runs) RUNS="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

{
    echo
    echo "=== measure-loop-curve runs=${RUNS} $(date -Is) ==="
    echo "SENTINEL curve-start"
} >>"$OUTPUT"

for buf in "${BUFFERS[@]}"; do
    for loops in "${LOOPS_LIST[@]}"; do
        tag="buf${buf}-loops${loops}"
        echo "=== ${tag} ==="
        {
            echo "=== block ${tag} $(date -Is) ==="
        } >>"$OUTPUT"

        if ! "${SCRIPT_DIR}/measure-latency-run.sh" \
            --buffer "$buf" \
            --condition B \
            --runs "$RUNS" \
            --playing-loops "$loops" \
            --output "$OUTPUT"; then
            echo "ERROR: measure failed for ${tag}" >&2
            exit 1
        fi
    done
done

echo "SENTINEL curve-complete" | tee -a "$OUTPUT"
echo "Appended to $OUTPUT"
