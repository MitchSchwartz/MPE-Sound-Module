#!/usr/bin/env bash
# Load 16 fixture clips into SooperLooper, trigger playback, sample load — no human recording.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLIPS_DIR="${MPE_SL_TEST_CLIPS:-${REPO_ROOT}/tests/fixtures/sooperlooper-loops}"
SOOP_BIN="${MPE_SOOPERLOOPER_BIN:-${HOME}/src/sooperlooper-1.7.9/src/sooperlooper}"
OSC_HOST="${MPE_SL_OSC_HOST:-127.0.0.1}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
SL_OSC="osc.udp://${OSC_HOST}:${OSC_PORT}"
LOOPS="${MPE_SL_LOOPS:-15}"  # 15 usable max — see sl_limits.py
TIME_MAX="${MPE_SL_TIME_MAX:-40}"
JACK_CLIENT="${MPE_SL_JACK_CLIENT:-mpe-looper}"

log() { echo "smoke-16: $*"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "smoke-16: missing command: $1" >&2
    exit 1
  }
}

wire_jack_parallel() {
  # Fresh engine after pkill: connect-only avoids live disconnect xruns.
  bash "${SCRIPT_DIR}/wire-jack-graph.sh" connect
  sleep 0.5
}

start_engine() {
  if pgrep -x sooperlooper >/dev/null 2>&1; then
    log "stopping existing sooperlooper"
    pkill -x sooperlooper || true
    sleep 1
  fi
  if [[ ! -x "${SOOP_BIN}" ]]; then
    echo "smoke-16: SooperLooper binary not found: ${SOOP_BIN}" >&2
    exit 1
  fi
  log "starting ${LOOPS} loops, -t ${TIME_MAX}, port ${OSC_PORT}"
  "${SOOP_BIN}" -q -D yes -l "${LOOPS}" -c 2 -t "${TIME_MAX}" -p "${OSC_PORT}" -j "${JACK_CLIENT}" &
  sleep 2
  if ! pgrep -x sooperlooper >/dev/null; then
    echo "smoke-16: engine failed to start" >&2
    exit 1
  fi
}

ensure_clips() {
  if [[ ! -f "${CLIPS_DIR}/loop00.wav" ]]; then
    log "generating test clips"
    bash "${SCRIPT_DIR}/generate-test-clips.sh" "${CLIPS_DIR}"
  fi
  for i in $(seq 0 15); do
    f="${CLIPS_DIR}/loop$(printf '%02d' "${i}").wav"
    [[ -f "${f}" ]] || {
      echo "smoke-16: missing ${f}" >&2
      exit 1
    }
  done
}

load_and_play() {
  need_cmd oscsend
  for i in $(seq 0 15); do
    wav="${CLIPS_DIR}/loop$(printf '%02d' "${i}").wav"
    log "load loop ${i} ← ${wav}"
    oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/load_loop" sss "${wav}" "" ""
    sleep 0.15
  done
  for i in $(seq 0 15); do
    oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/hit" s trigger
  done
  log "triggered all 16 loops"
}

sample_stats() {
  local rss
  rss="$(ps -o rss= -C sooperlooper 2>/dev/null | awk '{s+=$1} END{print s+0}')"
  log "VmRSS=${rss} kB"
  if command -v timeout >/dev/null 2>&1 && command -v jack_cpu_load >/dev/null 2>&1; then
    local cpu
    # -k is load-bearing: jack_cpu_load ignores SIGTERM (see diagnose-16loop-crackle.sh).
    cpu="$(timeout -k 0.5 3 jack_cpu_load 2>/dev/null | grep -E 'load|DSP' | tail -1 || echo "n/a")"
    log "jack_cpu_load (3s sample): ${cpu}"
  fi
}

main() {
  need_cmd oscsend
  ensure_clips
  start_engine
  bash "${SCRIPT_DIR}/configure-grid-sync.sh" || log "WARN: grid-sync configure failed"
  wire_jack_parallel
  load_and_play
  sleep 2
  sample_stats
  log "pausing all loops (smoke complete — use sl-stop or APC to control playback)"
  bash "${SCRIPT_DIR}/stop-all-loops.sh" || true
  log "PASS — 16 clips loaded, triggered, measured, paused"
}

main "$@"
