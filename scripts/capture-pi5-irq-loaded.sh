#!/usr/bin/env bash
# Pi 5 IRQ census — loaded snapshot (Phase 0.2).
# Runs midi-load-hold under Surge, logs throttle/temp, captures interrupts.
#
# Usage on Pi:
#   ./scripts/capture-pi5-irq-loaded.sh [VOICES] [SECONDS]
# Default: 24 voices (midi-load-hold max), 60 s — reduce if no cooler / marginal PSU.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATE_TAG="$(date +%Y-%m-%d)"
OUT="${OUT_DIR:-$REPO_ROOT/appliance-state/pi5-irq-census-$DATE_TAG}"
VOICES="${1:-24}"
SECS="${2:-60}"

if [ "$VOICES" -gt 24 ] || [ "$VOICES" -lt 1 ]; then
    echo "ERROR: VOICES must be 1..24 (midi-load-hold limit)" >&2
    exit 1
fi

mkdir -p "$OUT"

if ! systemctl is-active --quiet mpe-jackd || ! systemctl is-active --quiet surge-xt-cli; then
    echo "ERROR: mpe-jackd and surge-xt-cli must be active" >&2
    exit 1
fi

_throttle_log() {
    while true; do
        printf '%s throttled=%s temp=%s arm_mhz=%s\n' \
            "$(date -Is)" \
            "$(vcgencmd get_throttled 2>/dev/null || echo n/a)" \
            "$(vcgencmd measure_temp 2>/dev/null || echo n/a)" \
            "$(vcgencmd measure_clock arm 2>/dev/null | sed 's/frequency(0)=//' || echo n/a)"
        sleep 10
    done
}

{
    echo "=== loaded capture metadata ==="
    echo "started: $(date -Is)"
    echo "voices: $VOICES"
    echo "duration_s: $SECS"
    echo "jack: $(ps aux | grep '[j]ackd' | head -1 || true)"
    grep -E '^MPE_JACK' /etc/mpe/mpe.env 2>/dev/null || true
    echo ""
    echo "=== hardware caveats (operator) ==="
    echo "No active cooler fitted; PSU reported 5V/3A (below official 27W/5A)."
    echo "Expect thermal and/or undervoltage flags under load — log below."
    echo ""
} >"$OUT/loaded-metadata.txt"

_throttle_log >"$OUT/throttle-series.log" &
LOG_PID=$!
trap 'kill "$LOG_PID" 2>/dev/null || true' EXIT

echo "Pre-load interrupts snapshot..."
chmod -R u+w "$OUT" 2>/dev/null || true
cp /proc/interrupts "$OUT/interrupts-preload.txt"

echo "Load: midi-load-hold ${SECS}s ${VOICES} voices..."
if ! python3 "$SCRIPT_DIR/midi-load-hold.py" "$((SECS + 5))" "$VOICES" >"$OUT/midi-load.log" 2>&1; then
    echo "WARN: midi-load-hold failed — see $OUT/midi-load.log" >&2
fi

sleep 2
cp /proc/interrupts "$OUT/interrupts-loaded.txt"
cp /proc/softirqs "$OUT/softirqs-loaded.txt"

kill "$LOG_PID" 2>/dev/null || true
wait "$LOG_PID" 2>/dev/null || true
trap - EXIT

{
    echo "finished: $(date -Is)"
    echo "throttled_final: $(vcgencmd get_throttled 2>/dev/null || echo n/a)"
    echo ""
    echo "=== IRQ delta (key lines, loaded minus pre-load counts) ==="
    for irq in 111 131 148 161 162; do
        pre="$(awk -v i="$irq" '$1 ~ "^"i":" {print $2; exit}' "$OUT/interrupts-preload.txt" 2>/dev/null || echo 0)"
        post="$(awk -v i="$irq" '$1 ~ "^"i":" {print $2; exit}' "$OUT/interrupts-loaded.txt" 2>/dev/null || echo 0)"
        echo "IRQ $irq: pre=$pre post=$post delta=$((post - pre))"
    done
} >>"$OUT/loaded-metadata.txt"

cat "$OUT/loaded-metadata.txt"

echo ""
echo "Wrote loaded capture under $OUT"
