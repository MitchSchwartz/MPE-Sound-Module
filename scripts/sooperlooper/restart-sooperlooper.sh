#!/usr/bin/env bash
# Restart SooperLooper on JACK and restore the eval graph after jackd restarts.
#
# Smoke/eval starts sooperlooper manually (not mpe-looper.service). A jackd
# restart leaves a live process that is no longer on the bus — record path dead,
# crackle from Surge-only + orphan CPU. This script detects that and fixes it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOOP_BIN="${MPE_SOOPERLOOPER_BIN:-${HOME}/src/sooperlooper-1.7.9/src/sooperlooper}"
OSC_HOST="${MPE_SL_OSC_HOST:-127.0.0.1}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
LOOPS="${MPE_SL_LOOPS:-16}"
TIME_MAX="${MPE_SL_TIME_MAX:-40}"
JACK_CLIENT="${MPE_SL_JACK_CLIENT:-mpe-looper}"
ENGINE_LOG="${MPE_SL_ENGINE_LOG:-/tmp/sooperlooper.log}"

log() { echo "sl-restart: $*"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "sl-restart: missing command: $1" >&2
    exit 1
  }
}

jack_client_visible() {
  jack_lsp 2>/dev/null | grep -q "^${JACK_CLIENT}:"
}

record_path_ok() {
  jack_lsp -c "${JACK_CLIENT}:loop0_in_1" 2>/dev/null | grep -Fq "Surge XT:out_1" \
    && jack_lsp -c "${JACK_CLIENT}:loop$((LOOPS - 1))_in_1" 2>/dev/null | grep -Fq "Surge XT:out_1"
}

playback_path_ok() {
  jack_lsp -c "system:playback_1" 2>/dev/null | grep -Fq "${JACK_CLIENT}:common_out_1"
}

start_engine() {
  if pgrep -x sooperlooper >/dev/null 2>&1; then
    log "stopping existing sooperlooper"
    pkill -x sooperlooper || true
    sleep 1
  fi
  if [[ ! -x "${SOOP_BIN}" ]]; then
    echo "sl-restart: SooperLooper binary not found: ${SOOP_BIN}" >&2
    exit 1
  fi
  log "starting ${LOOPS} loops, -t ${TIME_MAX}, port ${OSC_PORT} (log: ${ENGINE_LOG})"
  # setsid + redirect, not a bare `&`. A backgrounded child that inherits stdout
  # holds the SSH channel open for as long as it lives, so `mpe looper
  # sl-restart` — the documented remedy for an orphan, and the thing you reach
  # for mid-session — never returns. It also leaves the engine in the session's
  # process group, where a SIGHUP on disconnect can take it down with the very
  # terminal you used to rescue it.
  setsid nohup "${SOOP_BIN}" -q -D yes -l "${LOOPS}" -c 2 -t "${TIME_MAX}" \
    -p "${OSC_PORT}" -j "${JACK_CLIENT}" >> "${ENGINE_LOG}" 2>&1 < /dev/null &
  disown 2>/dev/null || true
  sleep 2
  if ! pgrep -x sooperlooper >/dev/null; then
    echo "sl-restart: engine failed to start" >&2
    exit 1
  fi
  if ! jack_client_visible; then
    echo "sl-restart: process up but not on JACK — is mpe-jackd running?" >&2
    exit 1
  fi
}

main() {
  need_cmd jack_lsp
  if pgrep -x sooperlooper >/dev/null 2>&1 && jack_client_visible && record_path_ok && playback_path_ok; then
    log "OK — on JACK, record + playback paths wired"
    exit 0
  fi

  if pgrep -x sooperlooper >/dev/null 2>&1 && ! jack_client_visible; then
    log "orphan detected (process without JACK client)"
  elif pgrep -x sooperlooper >/dev/null 2>&1; then
    log "on JACK but graph incomplete — rewiring"
  else
    log "sooperlooper not running — starting"
  fi

  start_engine
  bash "${SCRIPT_DIR}/configure-grid-sync.sh" || log "WARN: grid-sync configure failed"
  bash "${SCRIPT_DIR}/wire-jack-graph.sh" connect
  sleep 0.5

  if record_path_ok && playback_path_ok; then
    log "PASS — Surge -> loop0_in, common_out -> playback"
  else
    echo "sl-restart: graph verify failed" >&2
    jack_lsp -c "Surge XT:out_1" 2>/dev/null || true
    exit 1
  fi
}

main "$@"
