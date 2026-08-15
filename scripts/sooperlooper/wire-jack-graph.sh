#!/usr/bin/env bash
# Parallel fail-open graph for SooperLooper eval (B1 + multi-loop listen path).
#
# Listen path: engine common_out -> playback (internal mix), NOT per-loop outs.
# Fail-open:   Surge -> playback stays connected.
# Record path: Surge -> every loopN_in (16-pad APC clip grid).
#
# Usage:
#   wire-jack-graph.sh [connect|rewire]
#     connect (default) — fresh engine: connect only, no disconnect pass (avoids xruns)
#     rewire            — fix bad graph: pause loops, disconnect per-loop outs, connect
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-connect}"

OSC_HOST="${MPE_SL_OSC_HOST:-127.0.0.1}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
LOOPS="${MPE_SL_LOOPS:-16}"
JACK_CLIENT="${MPE_SL_JACK_CLIENT:-mpe-looper}"
SURGE_CLIENT="${MPE_SL_SURGE_CLIENT:-Surge XT}"

FAILURES=0

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "wire-jack: missing: $1" >&2
    exit 1
  }
}

log() { echo "wire-jack: $*"; }

# Report real failures, but never abort the run.
#
# Two traps this has already fallen into:
#   1. Returning non-zero from a bare call under `set -e` kills the script. On
#      2026-08-14 that aborted the wiring pass on the FIRST already-connected
#      port, so common_out never reached playback: loops played silently and
#      the pad went green with no audio.
#   2. "already connected" / "not connected" are the DESIRED end state, not
#      errors. Counting them made a healthy graph report failures.
try_jack() {
  local out status
  out="$("$@" 2>&1)"
  status=$?
  if [ "$status" -eq 0 ]; then
    return 0
  fi
  case "$out" in
    *"already connected"* | *"not connected"* | *"cannot connect client, already"*)
      return 0
      ;;
  esac
  FAILURES=$((FAILURES + 1))
  log "FAILED: $* -- ${out:-exit $status}"
  return 0
}

try_oscsend() {
  if ! oscsend "${OSC_HOST}" "${OSC_PORT}" "$@"; then
    FAILURES=$((FAILURES + 1))
    log "FAILED: oscsend $*"
    return 1
  fi
}

disconnect_loop_outs_from_playback() {
  local i
  for i in $(seq 0 $((LOOPS - 1))); do
    try_jack jack_disconnect "${JACK_CLIENT}:loop${i}_out_1" "system:playback_1" 2>/dev/null || true
    try_jack jack_disconnect "${JACK_CLIENT}:loop${i}_out_2" "system:playback_2" 2>/dev/null || true
  done
}

connect_graph() {
  local i
  for i in $(seq 0 $((LOOPS - 1))); do
    try_jack jack_connect "${SURGE_CLIENT}:out_1" "${JACK_CLIENT}:loop${i}_in_1"
    try_jack jack_connect "${SURGE_CLIENT}:out_2" "${JACK_CLIENT}:loop${i}_in_2"
  done
  try_jack jack_connect "${SURGE_CLIENT}:out_1" "system:playback_1"
  try_jack jack_connect "${SURGE_CLIENT}:out_2" "system:playback_2"
  try_jack jack_connect "${JACK_CLIENT}:common_out_1" "system:playback_1"
  try_jack jack_connect "${JACK_CLIENT}:common_out_2" "system:playback_2"
}

set_dry_all() {
  local i
  for i in $(seq 0 $((LOOPS - 1))); do
    try_oscsend "/sl/${i}/set" sf dry 0.0
  done
}

wire_connect() {
  need_cmd jack_connect
  need_cmd oscsend
  log "connect-only (${MODE}): Surge -> loop0..$((LOOPS - 1)) in; common_out -> playback"
  connect_graph
  set_dry_all
  log "dry=0 on loops 0..$((LOOPS - 1))"
}

wire_rewire() {
  need_cmd jack_connect
  need_cmd oscsend
  log "rewire: pausing loops before graph surgery"
  bash "${SCRIPT_DIR}/stop-all-loops.sh" 2>/dev/null || true
  sleep 0.5
  log "disconnecting per-loop outs from playback (use common_out mix instead)"
  disconnect_loop_outs_from_playback
  connect_graph
  set_dry_all
  log "dry=0 on loops 0..$((LOOPS - 1)); common_out -> playback; Surge parallel fail-open"
}

case "${MODE}" in
  connect) wire_connect ;;
  rewire) wire_rewire ;;
  *)
    echo "wire-jack: unknown mode: ${MODE} (use connect|rewire)" >&2
    exit 1
    ;;
esac

if [[ "${FAILURES}" -gt 0 ]]; then
  log "${FAILURES} connection(s) failed — graph may be incomplete"
  exit 1
fi

log "graph wiring complete (${FAILURES} failures)"
exit 0
