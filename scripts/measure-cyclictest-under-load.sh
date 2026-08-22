#!/bin/bash
# cyclictest under live audio stack — Scarlett Step 2 (2026-08-21)
#
# Re-runs cyclictest with JACK's real-time priority (70) on a pinned CPU while
# the audio stack is up. Xruns during these windows are VOID (wakeup latency only).
#
# Usage:
#   ./scripts/measure-cyclictest-under-load.sh [--output FILE] [--label TAG] [--cpu N] [--seconds N]
#
# Default: CPU 3, priority 70, 300 s (5 min), 200 µs interval.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

OUTPUT="${MPE_CYCLICTEST_LOG:-$HOME/latency-cyclictest.log}"
LABEL="under-load-cpu3"
CPU=3
PRIORITY=70
INTERVAL=200
SECONDS=300
LOOPS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --label) LABEL="${2:?}"; shift 2 ;;
        --cpu) CPU="${2:?}"; shift 2 ;;
        --priority) PRIORITY="${2:?}"; shift 2 ;;
        --seconds) SECONDS="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if ! command -v cyclictest >/dev/null 2>&1; then
    echo "ERROR: cyclictest not found" >&2
    exit 1
fi

LOOPS=$(( (SECONDS * 1000000) / INTERVAL ))

echo "=== cyclictest under load label=${LABEL} cpu=${CPU} p=${PRIORITY} ${SECONDS}s loops=${LOOPS} $(date -Is) ==="
echo "NOTE: xruns during this window are VOID — measuring wakeup latency only."

if ! systemctl is-active --quiet mpe-jackd.service 2>/dev/null; then
    echo "WARNING: mpe-jackd not active — run under real audio load for valid comparison" >&2
fi

RAW="$(taskset -c "$CPU" cyclictest -m -t1 -p "$PRIORITY" -i "$INTERVAL" -l "$LOOPS" -a "$CPU" 2>&1)"
RC=$?

if [ "$RC" -ne 0 ]; then
    echo "ERROR: cyclictest exited $RC" >&2
    printf '%s\n' "$RAW" | head -10 >&2
    exit 1
fi

if ! grep -qE 'Min:[[:space:]]*[0-9]+.*Max:[[:space:]]*[0-9]+' <<<"$RAW"; then
    echo "ERROR: no Min/Max line in cyclictest output" >&2
    printf '%s\n' "$RAW" | head -10 >&2
    exit 1
fi

MAXUS="$(grep -oE 'Max:[[:space:]]*[0-9]+' <<<"$RAW" | grep -oE '[0-9]+' | sort -n | tail -1)"
AVGUS="$(grep -oE 'Avg:[[:space:]]*[0-9]+' <<<"$RAW" | grep -oE '[0-9]+' | sort -n | tail -1)"

{
    echo "=== cyclictest under-load label=${LABEL} $(date -Is) ==="
    echo "cpu=${CPU} priority=${PRIORITY} interval_us=${INTERVAL} seconds=${SECONDS}"
    echo "jack_active=$(systemctl is-active mpe-jackd.service 2>/dev/null || echo unknown)"
    echo "SENTINEL cyclictest-start"
    uname -r
    tr '\0' ' ' < /proc/cmdline 2>/dev/null || true
    echo
    printf '%s\n' "$RAW"
    echo "WORST_CASE_USEC=${MAXUS}"
    echo "AVG_USEC=${AVGUS}"
    echo "SENTINEL cyclictest-end"
    echo
} >>"$OUTPUT"

echo "Appended to $OUTPUT — worst case ${MAXUS} us, avg ${AVGUS} us"
echo "SENTINEL cyclictest-logged"
