#!/usr/bin/env bash
# Parallel fail-open graph for SooperLooper eval (B1 + multi-loop listen path).
#
# Listen path: engine common_out -> playback (internal mix), NOT per-loop outs.
# Fail-open:   Surge -> playback stays connected.
# Record path: Surge -> loop0_in (single-pad bench default; extend per product).
set -euo pipefail

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

wire_graph() {
  need_cmd jack_connect
  need_cmd oscsend

  log "disconnecting per-loop outs from playback (use common_out mix instead)"
  disconnect_loop_outs_from_playback

  jack_connect "${SURGE_CLIENT}:out_1" "${JACK_CLIENT}:loop0_in_1" 2>/dev/null || true
  jack_connect "${SURGE_CLIENT}:out_2" "${JACK_CLIENT}:loop0_in_2" 2>/dev/null || true
  jack_connect "${SURGE_CLIENT}:out_1" "system:playback_1" 2>/dev/null || true
  jack_connect "${SURGE_CLIENT}:out_2" "system:playback_2" 2>/dev/null || true
  jack_connect "${JACK_CLIENT}:common_out_1" "system:playback_1" 2>/dev/null || true
  jack_connect "${JACK_CLIENT}:common_out_2" "system:playback_2" 2>/dev/null || true

  local i
  for i in $(seq 0 $((LOOPS - 1))); do
    oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/set" sf dry 0.0 2>/dev/null || true
  done
  log "dry=0 on loops 0..$((LOOPS - 1)); common_out -> playback; Surge parallel fail-open"
}

wire_graph "$@"
