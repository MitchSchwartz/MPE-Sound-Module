#!/bin/bash
# T7a — periods-per-buffer sharp test (condition D, 16 loops playing).
#
#   256 x 6  (32 ms, +25% cushion vs 512 x 3)
#   512 x 3  (32 ms, known-failing baseline)
#   256 x 8  (42.7 ms, safe variant)
#
# Usage:
#   sudo ./scripts/measure-t7a.sh [--runs 15] [--output FILE]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUNS=15
OUTPUT="${MPE_T7A_LOG:-$HOME/t7a-periods.log}"
LOOPS=16

while [ $# -gt 0 ]; do
    case "$1" in
        --runs) RUNS="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

{
    echo
    echo "=== measure-t7a runs=${RUNS} loops=${LOOPS} $(date -Is) ==="
    echo "SENTINEL t7a-start"
} >>"$OUTPUT"

_run_block() {
    local buf="$1" periods="$2" tag="$3"
    echo "=== ${tag} (buffer=${buf} periods=${periods}) ==="
    echo "=== block ${tag} $(date -Is) ===" >>"$OUTPUT"
    "${SCRIPT_DIR}/measure-latency-run.sh" \
        --buffer "$buf" \
        --periods "$periods" \
        --condition D \
        --runs "$RUNS" \
        --playing-loops "$LOOPS" \
        --output "$OUTPUT"
}

_run_block 256 6 "buf256-p6-loops16"
_run_block 512 3 "buf512-p3-loops16"
_run_block 256 8 "buf256-p8-loops16"

echo "SENTINEL t7a-complete" | tee -a "$OUTPUT"
echo "Appended to $OUTPUT"
