#!/usr/bin/env bash
# Full track reset: pause + clear every loop (no engine restart).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OSC_HOST="${MPE_SL_OSC_HOST:-127.0.0.1}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
LOOPS="${MPE_SL_LOOPS:-16}"

if ! pgrep -x sooperlooper >/dev/null 2>&1; then
  echo "reset-all-loops: sooperlooper not running"
  exit 0
fi

bash "${SCRIPT_DIR}/stop-all-loops.sh"
for i in $(seq 0 $((LOOPS - 1))); do
  oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/hit" s undo_all 2>/dev/null || true
done
oscsend "${OSC_HOST}" "${OSC_PORT}" /sl/-1/hit s undo_all 2>/dev/null || true
echo "reset-all-loops: paused + cleared loops 0..$((LOOPS - 1))"
