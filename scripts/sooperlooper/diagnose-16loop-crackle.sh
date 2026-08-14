#!/usr/bin/env bash
# B7/B14: distinguish xruns vs gain clipping when 16 SooperLooper loops + Surge play.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DUR="${MPE_SL_DIAG_SEC:-45}"
CAP_SEC="${MPE_SL_DIAG_CAPTURE_SEC:-5}"
OSC_HOST="${MPE_SL_OSC_HOST:-127.0.0.1}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
LOOPS="${MPE_SL_LOOPS:-16}"
OUT="${MPE_SL_DIAG_OUT:-/tmp/sl-16loop-diag-$(date +%Y%m%d-%H%M%S).txt}"

log() { echo "diag-16: $*" | tee -a "$OUT"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "diag-16: missing: $1" >&2
    exit 1
  }
}

playback_status_path() {
  local card
  if [ -r /proc/asound/card1/pcm0p/sub0/status ]; then
    echo /proc/asound/card1/pcm0p/sub0/status
    return
  fi
  for card in /proc/asound/card*/pcm0p/sub0/status; do
    if [ -r "$card" ]; then
      echo "$card"
      return
    fi
  done
  echo ""
}

read_xrun() {
  local path="$1"
  if [ -z "$path" ] || [ ! -r "$path" ]; then
    echo "n/a"
    return
  fi
  # JACK-held ALSA nodes often omit xrun:; use overrun/underflow if present.
  local x
  x="$(awk '/^xrun:/ {print $2; exit}' "$path" 2>/dev/null)"
  if [ -n "$x" ]; then
    echo "$x"
    return
  fi
  local u o
  u="$(awk '/^underflow/ {print $3; exit}' "$path" 2>/dev/null)"
  o="$(awk '/^overrun/ {print $3; exit}' "$path" 2>/dev/null)"
  echo "${u:-0}/${o:-0}"
}

count_playback_inputs() {
  if ! command -v jack_lsp >/dev/null 2>&1; then
    echo "n/a"
    return
  fi
  jack_lsp -c 2>/dev/null | awk '
    /^system:playback/ { inblock=1; next }
    inblock && /^[[:space:]]/ { count++; next }
    inblock { inblock=0 }
    END { print count+0 }
  '
}

sample_cpu() {
  if command -v timeout >/dev/null 2>&1 && command -v jack_cpu_load >/dev/null 2>&1; then
    timeout 3 jack_cpu_load 2>/dev/null | tail -1 || echo "n/a"
  else
    echo "n/a"
  fi
}

capture_peak_db() {
  local label="$1"
  local wav="/tmp/sl-diag-capture-${label}.wav"
  if ! command -v jack_capture >/dev/null 2>&1; then
    echo "jack_capture_not_installed"
    return
  fi
  need_cmd ffmpeg
  rm -f "$wav"
  timeout "$((CAP_SEC + 2))" jack_capture -d "$CAP_SEC" -f wav "$wav" >/dev/null 2>&1 || true
  if [ ! -s "$wav" ]; then
    echo "capture_failed"
    return
  fi
  ffmpeg -hide_banner -i "$wav" -af volumedetect -f null - 2>&1 \
    | awk -F': ' '/max_volume/ {print $2; exit}' || echo "n/a"
  rm -f "$wav"
}

ensure_16_playing() {
  need_cmd oscsend
  local clips="${MPE_SL_TEST_CLIPS:-${REPO_ROOT}/tests/fixtures/sooperlooper-loops}"
  if ! pgrep -x sooperlooper >/dev/null; then
    log "starting engine via smoke script"
    bash "${SCRIPT_DIR}/smoke-16-loops.sh" >>"$OUT" 2>&1
    return
  fi
  log "sooperlooper already running — re-triggering loops"
  for i in $(seq 0 $((LOOPS - 1))); do
    wav="${clips}/loop$(printf '%02d' "${i}").wav"
    if [ -f "$wav" ]; then
      oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/load_loop" sss "${wav}" "" "" 2>/dev/null || true
    fi
    oscsend "${OSC_HOST}" "${OSC_PORT}" "/sl/${i}/hit" s trigger 2>/dev/null || true
  done
}

main() {
  need_cmd oscsend
  : >"$OUT"
  log "=== 16-loop crackle diagnostic ==="
  log "duration=${DUR}s capture=${CAP_SEC}s loops=${LOOPS}"

  local status_path
  status_path="$(playback_status_path)"
  log "alsa status: ${status_path:-missing}"

  if ! pgrep -x jackd >/dev/null; then
    log "ERROR: jackd not running"
    exit 1
  fi

  log "jack buffer: $(jack_bufsize 2>/dev/null | tail -1 || echo n/a)"
  log "jack rate: $(jack_samplerate 2>/dev/null | tail -1 || echo n/a)"
  log "playback fan-in (connections -> system:playback): $(count_playback_inputs)"

  local x0 x1
  x0="$(read_xrun "$status_path")"
  log "xrun baseline: ${x0}"

  ensure_16_playing
  sleep 2

  log "jack_cpu_load (loops playing): $(sample_cpu)"
  log "peak capture (loops+surge graph): $(capture_peak_db all) dBFS"

  log "soaking ${DUR}s with 16 loops + Surge path active..."
  sleep "$DUR"

  x1="$(read_xrun "$status_path")"
  log "xrun after soak: ${x1}"
  log "xrun delta: $((x1 - x0)) (if numeric)"
  log "jack_cpu_load (end): $(sample_cpu)"

  if command -v journalctl >/dev/null 2>&1; then
    local jr
    jr="$(journalctl -u mpe-jackd.service --since "${DUR} seconds ago" --no-pager 2>/dev/null \
      | grep -ciE 'xrun|Xrun' || true)"
    log "mpe-jackd journal xrun mentions (last ${DUR}s): ${jr}"
  fi

  log "=== graph fan-in detail ==="
  jack_lsp -c 2>/dev/null | awk '
    /^system:playback/ { p=$0; next }
    p != "" && $0 !~ /^[[:space:]]/ { p="" }
    p != "" { print }
  ' | head -40 | tee -a "$OUT"

  log "=== interpretation hints ==="
  log "fan-in > 8 + peak near 0 dBFS -> likely gain clipping (16 loops sum at playback)"
  log "xrun delta > 0 or jack CPU > 60% -> likely buffer/CPU underrun crackle"
  log "full log: ${OUT}"
}

main "$@"
