#!/usr/bin/env bash
# Parallel fail-open graph for SooperLooper eval (B1 + multi-loop listen path).
#
# Listen path: engine common_out -> playback (internal mix), NOT per-loop outs.
# Fail-open:   the fan-out source -> playback stays connected unless an insert carries it.
# Record path: the fan-out source -> every loopN_in (16-pad APC clip grid).
#              Both decided by patch_browser/audio_routing.py, not here.
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
# Surge's client name, and every rule about where its output goes, live in
# the routing authority.
ROUTING="${SCRIPT_DIR}/../../patch_browser/audio_routing.py"

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

# Where Surge's live signal goes is not decided here. The routing authority
# (patch_browser/audio_routing.py) reads the graph as it is and answers with the
# edges to ADD: the fan-out source -- Surge, or the FX host when it is on the
# trunk -- into every loop input, and into playback unless the live monitor is
# carrying it. It never proposes an edge to a destination Surge already
# reaches by any route, so this pass cannot double the live signal underneath
# an insert. That used to be a hand-written `live_monitor_carrying` here, which
# knew one insert by name and would have reconnected Surge -> playback beneath
# the next one.
#
# One `jack_lsp -c` and one python3 per pass (~0.5 s on the Pi), replacing four
# `jack_lsp -c` calls and 32 unconditional jack_connects. Runs at engine start
# and on a watchdog repair, never on a timer.
connect_graph() {
  local plan rc src dst
  # A failed jack_lsp hands the authority an empty graph, which it answers
  # fail-open (everything direct) rather than with nothing.
  plan="$({ jack_lsp -c 2>/dev/null || true; } | python3 "${ROUTING}" plan --loops "${LOOPS}")" && rc=0 || rc=$?
  if [ "$rc" -ne 0 ]; then
    FAILURES=$((FAILURES + 1))
    log "FAILED: routing authority exited ${rc} -- Surge record/monitor path not wired"
  elif [ -z "$plan" ]; then
    log "Surge path: every destination already reached"
  else
    while IFS=$'\t' read -r src dst; do
      [ -n "$src" ] || continue
      try_jack jack_connect "$src" "$dst"
    done <<< "$plan"
    log "Surge path: $(printf '%s\n' "$plan" | wc -l) edge(s) added from the fan-out source"
  fi
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
  log "connect-only (${MODE}): fan-out source -> loop0..$((LOOPS - 1)) in; common_out -> playback"
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
  log "dry=0 on loops 0..$((LOOPS - 1)); common_out -> playback; fan-out source parallel fail-open"
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
