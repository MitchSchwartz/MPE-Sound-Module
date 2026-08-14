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

# -1 = all loops (SooperLooper OSC convention)
oscsend "${OSC_HOST}" "${OSC_PORT}" /sl/-1/hit s pause 2>/dev/null || true
for i in $(seq 0 $((LOOPS - 1))); do
  oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/hit" s pause 2>/dev/null || true
done
echo "stop-all-loops: paused loops 0..$((LOOPS - 1)) (and /sl/-1)"
