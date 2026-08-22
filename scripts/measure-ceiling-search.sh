#!/bin/bash
# Binary search max voice count with 0 xruns over confirm window (1024×3).
#
# Usage: sudo ./scripts/measure-ceiling-search.sh \
#            --patch-name "Closed Hat" --hi 12 --seconds 60 \
#            --artifact-dir /home/mitch/plan-v9c-...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
PATCH_NAME=""
HI=12
LO=1
SECONDS_HOLD=60
ARTIFACT_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        --hi) HI="${2:?}"; shift 2 ;;
        --lo) LO="${2:?}"; shift 2 ;;
        --seconds) SECONDS_HOLD="${2:?}"; shift 2 ;;
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

[ -n "$PATCH_NAME" ] && [ -n "$ARTIFACT_DIR" ] || {
    echo "ERROR: --patch-name --artifact-dir required" >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
exec > >(tee -a "${ARTIFACT_DIR}/ceiling-search.log") 2>&1

safe="${PATCH_NAME// /_}"
RESULTS="${ARTIFACT_DIR}/ceiling-search-${safe}.tsv"
printf 'voices\txruns\tclean\n' >"$RESULTS"

_try() {
    local v="$1"
    local log="${ARTIFACT_DIR}/confirm-v${v}.log"
    : >"$log"
    xr="$("$SCRIPT_DIR/measure-confirm-at-voices.sh" \
        --patch-name "$PATCH_NAME" --voices "$v" --seconds "$SECONDS_HOLD" \
        --output "$log" --tag "ceil-${safe}-v${v}")"
    echo "$xr"
}

echo "=== ceiling search patch=${PATCH_NAME} lo=${LO} hi=${HI} sec=${SECONDS_HOLD} $(date -Is) ==="

best_clean=0
lo=$LO
hi=$HI

while [ "$lo" -le "$hi" ]; do
    mid=$(( (lo + hi) / 2 ))
    echo "--- probe voices=${mid} ---"
    xr="$(_try "$mid")"
    if [ "$xr" -eq 0 ]; then
        printf '%s\t%s\tyes\n' "$mid" "$xr" >>"$RESULTS"
        best_clean=$mid
        lo=$((mid + 1))
    else
        printf '%s\t%s\tno\n' "$mid" "$xr" >>"$RESULTS"
        hi=$((mid - 1))
    fi
    sleep 2
done

echo "RESULT ceiling_search patch=${PATCH_NAME} max_clean_60s=${best_clean} hi_bound=${HI}"
echo "SENTINEL ceiling-search-complete patch=${PATCH_NAME} max_clean=${best_clean}"
column -t -s $'\t' "$RESULTS" || cat "$RESULTS"
