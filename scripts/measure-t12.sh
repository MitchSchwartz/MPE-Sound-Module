#!/bin/bash
# T12 — USB frame-aligned periods, condition A.
#
#   192 x 3  (exactly 4 ms USB frames @ 48 kHz) vs 256 x 3 (misaligned)
#   96  x 3  (exactly 2 ms) vs 128 x 3 (misaligned)
#
# Usage:
#   sudo ./scripts/measure-t12.sh [--runs 15] [--output FILE]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUNS=15
OUTPUT="${MPE_T12_LOG:-$HOME/t12-condA.log}"

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
    echo "=== measure-t12 runs=${RUNS} $(date -Is) ==="
    echo "SENTINEL t12-start"
} >>"$OUTPUT"

_run() {
    local buf="$1" periods="$2" tag="$3"
    echo "=== ${tag} buffer=${buf} periods=${periods} $(date -Is) ===" >>"$OUTPUT"
    "${SCRIPT_DIR}/measure-latency-run.sh" \
        --buffer "$buf" \
        --periods "$periods" \
        --condition A \
        --runs "$RUNS" \
        --no-restore-buffer \
        --output "$OUTPUT"
}

_run 192 3 "aligned-192x3"
_run 256 3 "misaligned-256x3"
_run 96 3 "aligned-96x3"
_run 128 3 "misaligned-128x3"

"${SCRIPT_DIR}/set-surge-audio.sh" --buffer 1024 --periods 3

echo "SENTINEL t12-complete $(date -Is)" | tee -a "$OUTPUT"
