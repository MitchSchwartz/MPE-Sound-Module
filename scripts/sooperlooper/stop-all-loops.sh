#!/usr/bin/env bash
# Pause every SooperLooper loop (eval recovery — smoke/diag leave loops triggered).
set -euo pipefail

OSC_HOST="${MPE_SL_OSC_HOST:-127.0.0.1}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
LOOPS="${MPE_SL_LOOPS:-16}"

if ! command -v oscsend >/dev/null 2>&1; then
  echo "stop-all-loops: oscsend required" >&2
  exit 1
fi

if ! pgrep -x sooperlooper >/dev/null 2>&1; then
  echo "stop-all-loops: sooperlooper not running"
  exit 0
fi

# pause_on, never pause. `pause` is a TOGGLE: this script used to hit /sl/-1
# (all loops) and then every loop again individually, so each loop was paused
# and immediately un-paused. "Stop all" left everything running.
#
# pause_on is idempotent, which is the whole point of a recovery script.
#
# -1 = all loops (SooperLooper OSC convention). The per-loop pass stays as a
# belt-and-braces fallback and is now safe to repeat.
oscsend "${OSC_HOST}" "${OSC_PORT}" /sl/-1/hit s pause_on 2>/dev/null || true
for i in $(seq 0 $((LOOPS - 1))); do
  oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/hit" s pause_on 2>/dev/null || true
done
echo "stop-all-loops: paused loops 0..$((LOOPS - 1)) (and /sl/-1)"
