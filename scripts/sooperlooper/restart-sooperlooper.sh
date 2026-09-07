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
LOOPS="${MPE_SL_LOOPS:-15}"  # 15 usable max — see sl_limits.py
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

restart_unit() {
  # systemd owns the engine. Since 2026-09-07 mpe-sooperlooper.service is
  # enabled at boot and mpe-looper-session.service Wants= it, so the old
  # move here -- stop the unit, launch a manual engine, "manual bench owns
  # the engine" -- turned every deploy into a hand-over: the session restart
  # pulled the unit back in, run-sooperlooper.sh reaped the manual engine as
  # a stray, the unit's graph verify failed against the half-torn-down
  # graph, and Restart=always brought a THIRD engine up five seconds later.
  # MEASURED 2026-09-07 13:26:03-13:26:26 on the SD image: the freshly
  # restarted session sat on a dead engine for six seconds. So when the unit
  # is enabled, the unit is restarted and nothing else starts an engine. Its
  # ExecStartPost wires the graph, applies grid sync and emits
  # looper.engine.started; this only waits for that to have happened.
  log "restarting mpe-sooperlooper.service — systemd owns the engine"
  if ! sudo systemctl restart mpe-sooperlooper.service; then
    # Restart=always retries in 5 s; a start whose ExecStartPost lost a
    # race is not yet a failed engine. The wait below decides.
    log "WARN: systemctl restart returned non-zero — waiting for the unit's retry"
  fi
  local i
  for i in $(seq 1 40); do
    if systemctl is-active --quiet mpe-sooperlooper.service \
       && jack_client_visible && record_path_ok && playback_path_ok; then
      log "PASS — Surge -> loop0_in, common_out -> playback (mpe-sooperlooper.service, ${i}x0.5s)"
      return 0
    fi
    sleep 0.5
  done
  echo "sl-restart: mpe-sooperlooper.service did not come up with a wired graph in 20 s" >&2
  systemctl status mpe-sooperlooper.service --no-pager 2>/dev/null | tail -n 8 >&2 || true
  jack_lsp -c "Surge XT:out_1" 2>/dev/null || true
  return 1
}

main() {
  need_cmd jack_lsp
  if command -v systemctl >/dev/null 2>&1 \
     && systemctl is-enabled --quiet mpe-sooperlooper.service 2>/dev/null; then
    restart_unit
    exit $?
  fi
  # No enabled unit: the manual path. A unit that exists but is disabled is
  # the pre-2026-09-07 opt-in layout, where a stray unit instance must not
  # be left competing with the engine started here.
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet mpe-sooperlooper.service 2>/dev/null; then
      log "stopping mpe-sooperlooper.service — manual bench owns the engine"
      sudo systemctl stop mpe-sooperlooper.service 2>/dev/null \
        || log "WARN: could not stop mpe-sooperlooper.service (sudo?)"
      sleep 0.5
    fi
  fi
  local sl_count=0
  if pgrep -x sooperlooper >/dev/null 2>&1; then
    sl_count="$(pgrep -xc sooperlooper)"
  fi
  if [[ "${sl_count}" -eq 1 ]] && jack_client_visible && record_path_ok && playback_path_ok; then
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
    # Tell the bench the engine is new, or it keeps applying the old one's grid
    # to a process that has never heard of it. `start_engine` launches the
    # binary directly (setsid nohup), NOT via systemd, so the ExecStartPost on
    # mpe-sooperlooper.service that normally emits this never fires here — the
    # systemd path is fine and this one was the split brain. Emitted after the
    # verify, because announcing a ready engine before the graph is proven is
    # the same lie one step earlier.
    # shellcheck source=../lib/audio-engine.sh
    source "${SCRIPT_DIR}/../lib/audio-engine.sh"
    mpe_session_event_emit looper.engine.started "sl-restart" || \
        log "WARN: could not emit looper.engine.started"
  else
    echo "sl-restart: graph verify failed" >&2
    jack_lsp -c "Surge XT:out_1" 2>/dev/null || true
    exit 1
  fi
}

main "$@"
