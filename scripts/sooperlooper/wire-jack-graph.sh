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

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "wire-jack: missing: $1" >&2
    exit 1
  }
}

log() { echo "wire-jack: $*"; }

disconnect_loop_outs_from_playback() {
  local i
  for i in $(seq 0 $((LOOPS - 1))); do
    jack_disconnect "${JACK_CLIENT}:loop${i}_out_1" "system:playback_1" 2>/dev/null || true
    jack_disconnect "${JACK_CLIENT}:loop${i}_out_2" "system:playback_2" 2>/dev/null || true
  done
}

connect_graph() {
  local i
  for i in $(seq 0 $((LOOPS - 1))); do
    jack_connect "${SURGE_CLIENT}:out_1" "${JACK_CLIENT}:loop${i}_in_1" 2>/dev/null || true
    jack_connect "${SURGE_CLIENT}:out_2" "${JACK_CLIENT}:loop${i}_in_2" 2>/dev/null || true
  done
  jack_connect "${SURGE_CLIENT}:out_1" "system:playback_1" 2>/dev/null || true
  jack_connect "${SURGE_CLIENT}:out_2" "system:playback_2" 2>/dev/null || true
  jack_connect "${JACK_CLIENT}:common_out_1" "system:playback_1" 2>/dev/null || true
  jack_connect "${JACK_CLIENT}:common_out_2" "system:playback_2" 2>/dev/null || true
}

set_dry_all() {
  local i
  for i in $(seq 0 $((LOOPS - 1))); do
    oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/set" sf dry 0.0 2>/dev/null || true
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
