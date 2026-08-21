#!/bin/bash
# Correlate xruns against DSP load, once a second, side by side.
#
# The tool that found the 2026-08-18 crackle: it showed 41 xruns/min landing at
# 41-55% DSP — nearly half the deadline unused — which ruled out load and pointed at
# something interrupting the graph. Reads xruns= from meter.state, so it needs
# mpe-peak-meter running (MPE_PEAK_METER=1) and works under shipping softmode, where
# journalctl reports nothing.
#
# Pair with scripts/midi-load.py for a reproducible load; start the load ~8 s first so
# the start transient is outside the window.
#   python3 scripts/midi-load.py 75 & sleep 8; scripts/xrun-corr.sh 60
#
# See docs/measurements/crackle-root-cause-2026-08-18.md
# Sample xruns + DSP load once per second and print them side by side.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

SECS="${1:-60}"
OUT=~/xrun-corr.out
: > "$OUT"
: > ~/corr-dsp.raw
stdbuf -oL jack_cpu_load > ~/corr-dsp.raw 2>/dev/null &
JCL=$!
trap "kill -9 $JCL 2>/dev/null" EXIT INT TERM HUP

xr() {
    mpe_meter_xruns_read
}

pk() {
    local file peak
    file="$(mpe_meter_state_file)"
    mpe_meter_assert_live || return 1
    peak="$(mpe_state_get "$file" peak_linear)"
    if [ -z "$peak" ]; then
        echo "ERROR: meter.state missing peak_linear=" >&2
        return 1
    fi
    printf '%s\n' "$peak"
}

if [ "$(mpe_read_appliance_env_var MPE_PEAK_METER 2>/dev/null || echo 0)" != "1" ]; then
    echo "ERROR: MPE_PEAK_METER is not 1 — xrun-corr requires meter.state" >&2
    exit 1
fi

PREV="$(xr)" || exit 1
START=$PREV
printf "  %4s %8s %8s %7s\n" "t" "dsp%" "peak" "xrun" >> "$OUT"
for ((i = 1; i <= SECS; i++)); do
    sleep 1
    CUR="$(xr)" || exit 1
    D=$(tail -1 ~/corr-dsp.raw | grep -oP "[0-9]+\.[0-9]+" | head -1)
    P="$(pk)" || exit 1
    DELTA=$((CUR - PREV))
    PREV=$CUR
    MARK=""
    [ "$DELTA" -gt 0 ] && MARK=" <<< XRUN x$DELTA"
    printf "  %4d %8s %8.3f %7d%s\n" "$i" "${D:-?}" "$P" "$CUR" "$MARK" >> "$OUT"
done
printf "TOTAL %d xruns in %ds meter_live=1\n" "$((PREV - START))" "$SECS" >> "$OUT"
kill -9 $JCL 2>/dev/null
