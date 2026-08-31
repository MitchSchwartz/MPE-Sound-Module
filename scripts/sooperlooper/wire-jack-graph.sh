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
LOOPS="${MPE_SL_LOOPS:-15}"  # 15 usable max — see sl_limits.py
STEM_CHANNELS="${MPE_USB_STEM_CHANNELS:-2}"
STEM_FIRST_CH=3   # 1/2 are the master pair
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
  # `out=$(cmd)` is itself a simple command: under `set -e` a non-zero cmd
  # aborts the script HERE, before any status check below can run. Guard the
  # assignment. (Third time this file has been bitten by set -e — 2026-08-14.)
  out="$("$@" 2>&1)" && status=0 || status=$?
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

# Per-loop stems on playback 3.. — only when the gadget is running multichannel.
#
# Both loopN_out_1 and loopN_out_2 go to the SAME playback port. JACK sums
# multiple sources into one port, so this is a real mono fold-down of a stereo
# loop, not the left channel with the right discarded.
#
# Gated on the ports actually existing rather than on the env var alone: with
# jackd on the Sound Blaster there are only two playback ports, and every
# connection here would be logged as a failure for a graph that is in fact
# correct — the same "reads the same whether it works or not" trap the rest of
# this file is annotated for.
connect_stems() {
  if [ "$STEM_CHANNELS" -le 2 ] 2>/dev/null; then
    return 0
  fi
  local available
  available="$(jack_lsp 2>/dev/null | grep -c '^system:playback_' || true)"
  if [ "${available:-0}" -lt "$STEM_FIRST_CH" ]; then
    log "stems: only ${available:-0} playback port(s) — not multichannel, skipping"
    return 0
  fi
  local wired=0 i ch
  for i in $(seq 0 $((LOOPS - 1))); do
    ch=$((STEM_FIRST_CH + i))
    [ "$ch" -le "$available" ] || break
    try_jack jack_connect "${JACK_CLIENT}:loop${i}_out_1" "system:playback_${ch}"
    try_jack jack_connect "${JACK_CLIENT}:loop${i}_out_2" "system:playback_${ch}"
    wired=$((wired + 1))
  done
  log "stems: loop0..$((wired - 1)) -> playback_${STEM_FIRST_CH}..$((STEM_FIRST_CH + wired - 1)) (mono fold, ${available} ports available)"
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
  connect_stems
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
  connect_stems
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
